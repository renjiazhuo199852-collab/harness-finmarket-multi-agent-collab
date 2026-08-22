"""解析用户金融查询，并提取后续模块需要的受控文本片段。

本模块同时保留两种解析协议：

* ``parse_user_query`` 是旧的独立路线兼容协议，仍然返回 ``QueryRoute``；
* ``parse_query_understanding`` 是统一入口使用的新协议，不返回固定路线，
  只提取查询主体、供应商、时间和检索改写文本，后续数据集意图由
  ``source.dataset_catalog`` 的候选记录决定。

无论使用哪种协议，对话大模型都不会选择 ``instrument_id``、``dataset_id``、
表名、物理列名或 SQL。金融工具和数据集仍然必须经过各自的混合检索、source
目录回查和程序校验。
"""

from __future__ import annotations

from enum import Enum
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .env_config import load_project_env


# 四条业务路线是模型和程序之间唯一允许的协议值。模型不能自行扩展路线名称。
class QueryRoute(str, Enum):
    """系统当前支持的四种金融数据查询路线。"""

    LATEST_PRICES = "latest_prices"
    MACRO_OBSERVATIONS = "macro_observations"
    MARKET_BARS = "market_bars"
    NEWS_ARTICLES = "news_articles"


DEFAULT_CHAT_PATH = "/chat/completions"
REQUEST_TIMEOUT = 90
QUERY_RELATION_SCOPES = frozenset({"direct", "related_to_subject"})


def _infer_relation_scope_from_query(query: str | None) -> str:
    """根据用户原文兜底识别“相关宏观指标”查询。

    ``direct`` 会把主体当作单个宏观指标，``related_to_subject`` 则会先确认
    金融工具，再读取正式的 ``source.instrument_metric_link`` 关系表。聊天模型
    偶尔会漏返回 ``relation_scope``，不能在字段缺失时无条件默认为 ``direct``，
    否则 ``EURUSD`` 会被错误当成宏观观测的 instrument_id。这里只依据用户原文
    的关系词和宏观业务词做有限、可解释的兜底，不生成任何数据集、指标 ID 或
    数据库对象。
    """

    if not query:
        return "direct"
    normalized_query = query.casefold()
    relation_terms = ("相关", "有关", "关联", "related", "relevant")
    macro_terms = (
        "宏观",
        "经济指标",
        "宏观指标",
        "政策利率",
        "利率",
        "债券收益率",
        "economic indicator",
        "macroeconomic",
        "interest rate",
        "bond yield",
    )
    if any(term.casefold() in normalized_query for term in relation_terms) and any(
        term.casefold() in normalized_query for term in macro_terms
    ):
        return "related_to_subject"
    return "direct"


def _chat_endpoint() -> str:
    """根据环境变量构造 OpenAI 兼容的聊天接口地址。"""

    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    chat_path = os.getenv("LLM_CHAT_COMPLETIONS_PATH", DEFAULT_CHAT_PATH)
    if not chat_path.startswith("/"):
        chat_path = "/" + chat_path
    return base_url + chat_path


def _extract_message_content(response_body: dict[str, Any]) -> str:
    """从兼容 OpenAI 的响应中提取模型返回文本。"""

    choices = response_body.get("choices") or []
    if not choices:
        raise RuntimeError("查询解析模型响应缺少 choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return "".join(text_parts)
    raise RuntimeError("查询解析模型响应缺少可解析的 message.content")


def _parse_json_content(content: str) -> dict[str, Any]:
    """解析模型 JSON，并兼容模型偶尔返回 Markdown JSON 代码块。"""

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    result = json.loads(text)
    if not isinstance(result, dict):
        raise RuntimeError("查询解析模型返回的 JSON 不是对象")
    return result


def _optional_text(value: Any, field_name: str) -> str | None:
    """把模型可选文本字段转换成稳定值，拒绝数组和对象等非文本结果。"""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"查询解析模型返回的 {field_name} 不是字符串或 null")
    clean_value = value.strip()
    return clean_value or None


def _is_original_span(original_query: str, extracted_text: str | None) -> bool:
    """判断提取结果是否确实来自用户原文，不允许模型偷偷翻译或标准化。"""

    if extracted_text is None:
        return True
    return extracted_text.casefold() in original_query.casefold()


def validate_query_parse_result(
    model_result: dict[str, Any],
    *,
    original_query: str | None = None,
) -> dict[str, Any]:
    """校验并裁剪模型结构化解析结果。

    ``instrument_text``、``provider_text`` 和 ``time_expression`` 必须是用户原文中
    的连续片段。这样模型只能提取用户实际说过的内容，不能把 ``EURUSD`` 擅自
    改写成 ``EUR/USD``，也不能凭空生成供应商代码。
    """

    raw_route = model_result.get("route")
    try:
        route = QueryRoute(raw_route)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in QueryRoute)
        raise ValueError(f"查询解析模型返回未知路线：{raw_route!r}，允许值：{allowed}") from exc

    raw_confidence = model_result.get("confidence", 0)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("查询解析模型返回的 confidence 不是数字") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("查询解析模型返回的 confidence 必须在 0 到 1 之间")

    reason = model_result.get("reason", "")
    if not isinstance(reason, str):
        raise ValueError("查询解析模型返回的 reason 不是字符串")

    instrument_text = _optional_text(model_result.get("instrument_text"), "instrument_text")
    instrument_search_text = _optional_text(
        model_result.get("instrument_search_text"),
        "instrument_search_text",
    )
    provider_text = _optional_text(model_result.get("provider_text"), "provider_text")
    time_expression = _optional_text(model_result.get("time_expression"), "time_expression")
    request_text = _optional_text(model_result.get("request_text"), "request_text")

    if original_query is not None:
        for field_name, field_value in (
            ("instrument_text", instrument_text),
            ("provider_text", provider_text),
            ("time_expression", time_expression),
        ):
            if not _is_original_span(original_query, field_value):
                raise ValueError(f"{field_name} 必须是用户原文中的连续文本片段")

    # 只向后续模块传递约定字段，丢弃表名、列名和 SQL 等额外模型输出。
    result = {
        "route": route.value,
        "confidence": confidence,
        "reason": reason,
        "instrument_text": instrument_text,
        "provider_text": provider_text,
        "time_expression": time_expression,
        "request_text": request_text,
    }
    # 旧调用方没有该字段时保持返回结构兼容；新模型提供时，它只用于目录召回，
    # 不能替代 instrument_text，也不能作为正式 instrument_id 使用。
    if instrument_search_text:
        result["instrument_search_text"] = instrument_search_text
    return result


def _call_chat_model(query: str) -> dict[str, Any]:
    """调用聊天模型，要求返回受控的查询解析 JSON。"""

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY，无法进行查询解析")

    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "high")
    allowed_routes = [route.value for route in QueryRoute]
    system_prompt = f"""你是金融数据查询解析器。
你必须从以下四个 route 中选择且只能选择一个：
{json.dumps(allowed_routes, ensure_ascii=False)}

你需要同时提取：
1. instrument_text：查询主体在用户原文中的连续文本片段。最新价格和历史行情的主体是金融工具；宏观路线的主体是指标、政策利率或债券收益率；新闻路线的主体是用户提到的金融工具或实体。例如 EURUSD、EUR/USD、欧元兑美元、美国 CPI；
2. instrument_search_text：仅供目录检索的英文短语。原文已经是英文或代码时原样复制；原文包含中文或其他语言时翻译为对应的英文业务表达，但不能补充用户没有说出的国家、指标类型、Core 等限定条件，也不能生成 ID、表名、列名或 SQL。例：美国 CPI -> US CPI，EURUSD -> EURUSD；
3. provider_text：供应商在用户原文中的连续文本片段，没有则为 null；
4. time_expression：时间范围在用户原文中的连续文本片段，例如最近一个月、2026-07-01 到 2026-07-31，没有则为 null；
5. request_text：用户要查询的业务请求短语，例如最新价格、日K线、宏观指标、相关新闻。

重要约束：
- instrument_text、provider_text、time_expression 必须逐字摘录用户原文，不能翻译、标准化或补全；
- 不要输出 instrument_id、dataset_id、表名、列名、JOIN 条件或 SQL；
- 如果问题中没有任何可检索主体，instrument_text 才能为 null；对于“查询美国 CPI 最新值”必须返回“美国 CPI”，不能返回 null；
- 如果没有供应商或时间条件，对应字段必须为 null。

宏观查询示例：
- “查询美国 CPI 最新值” -> instrument_text = “美国 CPI”；
- “查询美国联邦基金利率最新值” -> instrument_text = “美国联邦基金利率”；
- “查询美国 10 年期国债收益率” -> instrument_text = “美国 10 年期国债收益率”。

上述 instrument_text 必须逐字来自用户问题；示例中的空格、大小写和中文表达不应被模型自行改写。

分类规则：
- 最新价格、当前报价、买价、卖价、中间价 -> latest_prices；
- 宏观经济指标、经济数据、实际值、预测值、修订值 -> macro_observations；
- 历史行情、K线、开高低收、成交量、时间区间走势 -> market_bars；
- 新闻、文章、标题、摘要、内容、情绪 -> news_articles。

严格返回 JSON，不要返回 Markdown：
{{"route":"market_bars","instrument_text":"EURUSD","instrument_search_text":"EURUSD","provider_text":null,"time_expression":"最近一个月","request_text":"日K线","confidence":0.99,"reason":"用户询问 EURUSD 的历史日线行情"}}
"""
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        "temperature": 0,
        "reasoning_effort": reasoning_effort,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        _chat_endpoint(),
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"查询解析模型调用失败：{exc}") from exc
    return _parse_json_content(_extract_message_content(response_body))


def parse_user_query(query: str) -> dict[str, Any]:
    """解析完整用户问题，并返回经过程序校验的结构化查询文本。"""

    clean_query = query.strip()
    if not clean_query:
        raise ValueError("查询文本不能为空")
    return validate_query_parse_result(
        _call_chat_model(clean_query),
        original_query=clean_query,
    )


def _validate_search_terms(value: Any) -> list[str]:
    """校验模型生成的检索扩展词。

    扩展词只服务于召回，不能携带数据库对象。这里限制为非空短文本列表，
    既避免模型返回任意结构，也避免把表名、SQL 片段误传给检索模块。
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("query_understanding.search_terms 必须是字符串数组")
    terms: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("query_understanding.search_terms 只能包含字符串")
        term = item.strip()
        if term:
            terms.append(term)
    return list(dict.fromkeys(terms))


def validate_query_understanding_result(
    model_result: dict[str, Any],
    *,
    original_query: str | None = None,
) -> dict[str, Any]:
    """校验统一入口的无路线查询理解结果。

    ``subject_text``、``provider_text`` 和 ``time_expression`` 必须逐字来自用户
    原文；``subject_search_text``、``query_rewrite`` 和 ``search_terms`` 可以是
    多语言检索辅助文本。这样既保留用户真实输入，又允许中文问题获得英文语义
    召回能力，同时不把模型改写结果误当成正式主数据标识。
    """

    raw_confidence = model_result.get("confidence", 0)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("query_understanding.confidence 不是数字") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("query_understanding.confidence 必须在 0 到 1 之间")

    reason = model_result.get("reason", "")
    if not isinstance(reason, str):
        raise ValueError("query_understanding.reason 不是字符串")

    subject_text = _optional_text(model_result.get("subject_text"), "subject_text")
    subject_search_text = _optional_text(
        model_result.get("subject_search_text"),
        "subject_search_text",
    )
    provider_text = _optional_text(model_result.get("provider_text"), "provider_text")
    time_expression = _optional_text(model_result.get("time_expression"), "time_expression")
    request_text = _optional_text(model_result.get("request_text"), "request_text")
    query_rewrite = _optional_text(model_result.get("query_rewrite"), "query_rewrite")
    search_terms = _validate_search_terms(model_result.get("search_terms"))
    # 模型漏字段时，优先使用用户原文中的明确关系语义。即使模型显式返回
    # direct，只要原文明确询问“与某主体相关的宏观指标”，也必须走关系表，
    # 避免把金融工具主体误传给单指标宏观查询。
    inferred_relation_scope = _infer_relation_scope_from_query(original_query)
    raw_relation_scope = model_result.get("relation_scope")
    relation_scope = raw_relation_scope or inferred_relation_scope
    if inferred_relation_scope == "related_to_subject" and relation_scope == "direct":
        relation_scope = inferred_relation_scope
    if relation_scope not in QUERY_RELATION_SCOPES:
        raise ValueError(
            "query_understanding.relation_scope 只能是 direct 或 related_to_subject"
        )

    if original_query is not None:
        for field_name, field_value in (
            ("subject_text", subject_text),
            ("provider_text", provider_text),
            ("time_expression", time_expression),
        ):
            if not _is_original_span(original_query, field_value):
                raise ValueError(f"{field_name} 必须是用户原文中的连续文本片段")

    # 统一入口只传递受控的文本协议字段，丢弃模型可能额外输出的表名、字段名
    # 和 SQL，避免这些值进入后续数据库编排。
    return {
        "confidence": confidence,
        "reason": reason,
        "subject_text": subject_text,
        "subject_search_text": subject_search_text,
        "provider_text": provider_text,
        "time_expression": time_expression,
        "request_text": request_text,
        "query_rewrite": query_rewrite,
        "search_terms": search_terms,
        # 这是受控的查询关系语义，不是数据集、工具或指标 ID。后续只有宏观
        # 适配器会使用 related_to_subject，关系本身必须从正式关系表读取。
        "relation_scope": relation_scope,
    }


def _call_query_understanding_model(query: str) -> dict[str, Any]:
    """调用对话模型提取统一入口的查询理解字段。

    这里明确不要求模型识别 ``latest_prices`` 等固定路线。数据集目录中的记录
    才是业务意图候选，模型只负责提供检索主体、过滤条件和多语言召回辅助文本。
    """

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY，无法进行查询理解")

    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "high")
    system_prompt = """你是金融数据查询理解器。
你不能输出固定路线、dataset_id、instrument_id、表名、字段名、JOIN 条件或 SQL。
你只需要从用户原文中提取查询主体、供应商、时间表达和业务请求，并生成用于
数据集目录及业务文本检索的辅助文本。

字段要求：
1. subject_text：查询主体在用户原文中的连续片段，例如 EURUSD、EUR/USD、欧元兑美元、美国 CPI；没有主体时为 null；
2. subject_search_text：只用于检索的英文或多语言辅助表达。原文已经是代码或英文时可以原样复制；中文可以翻译，但不能添加用户未说出的限定条件；
3. provider_text：供应商在用户原文中的连续片段，没有则为 null；
4. time_expression：时间范围在用户原文中的连续片段，例如最近一个月、最近一周、2026-07-01 到 2026-07-31，没有则为 null；
5. request_text：用户请求的业务描述，例如最新价格、日K线、宏观指标、相关新闻；
6. query_rewrite：不改变原始意图的检索改写，可用于跨语言召回；
7. search_terms：不超过八个的主题扩展词，只能是自然语言词组，不能是 ID、表名、列名或 SQL。
8. relation_scope：如果用户明确询问“与某个金融工具相关的宏观指标”，返回
   related_to_subject；普通的“查询美国 CPI”或其他直接指标查询返回 direct。

重要约束：
- subject_text、provider_text、time_expression 必须逐字来自用户原文，不能标准化或凭空补全；
- 没有供应商时 provider_text 必须为 null，不要默认填入 LSEG；
- 日期范围单独返回，不要把日期改写成业务数据文本；
- query_rewrite 和 search_terms 只用于召回，不能替代正式目录或主数据确认；
- relation_scope 只描述 direct 或 related_to_subject，不得生成任何关系表、指标 ID
  或工具 ID；“相关”关系必须由服务端正式关系表确认；
- 严格返回 JSON，不要返回 Markdown。

返回格式：
{"subject_text":"EURUSD","subject_search_text":"EURUSD","provider_text":null,"time_expression":null,"request_text":"相关宏观指标","query_rewrite":"EUR/USD related macroeconomic indicators","search_terms":["EUR/USD","euro area indicators","US indicators"],"relation_scope":"related_to_subject","confidence":0.99,"reason":"..."}
"""
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        "temperature": 0,
        "reasoning_effort": reasoning_effort,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        _chat_endpoint(),
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"查询理解模型调用失败：{exc}") from exc
    return _parse_json_content(_extract_message_content(response_body))


def parse_query_understanding(query: str) -> dict[str, Any]:
    """解析统一接口的自然语言问题，不产生固定路线字段。"""

    clean_query = query.strip()
    if not clean_query:
        raise ValueError("查询文本不能为空")
    return validate_query_understanding_result(
        _call_query_understanding_model(clean_query),
        original_query=clean_query,
    )


# 项目启动时加载同一份环境变量配置，前端服务和命令行脚本保持一致。
load_project_env()
