"""使用聊天模型从已确认的金融工具候选中选择最终工具。

本模块只负责候选筛选，不访问数据库，也不生成 SQL。模型收到的候选已经由
``source.instrument_master`` 回查过；模型只能选择候选列表中的 ``instrument_id``。
最终选择还会由程序再次校验，防止模型返回未登记或非 active 的工具。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_CHAT_PATH = "/chat/completions"
REQUEST_TIMEOUT = 90


def _chat_endpoint() -> str:
    """拼接 OpenAI 兼容聊天接口地址。"""

    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    chat_path = os.getenv("LLM_CHAT_COMPLETIONS_PATH", DEFAULT_CHAT_PATH)
    if not chat_path.startswith("/"):
        chat_path = "/" + chat_path
    return base_url + chat_path


def _extract_message_content(response_body: dict[str, Any]) -> str:
    """从 OpenAI 兼容响应中提取模型文本，兼容字符串和内容块格式。"""

    choices = response_body.get("choices") or []
    if not choices:
        raise RuntimeError("聊天模型响应缺少 choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return "".join(text_parts)
    raise RuntimeError("聊天模型响应缺少可解析的 message.content")


def _parse_json_content(content: str) -> dict[str, Any]:
    """解析模型 JSON；兼容模型偶尔返回 Markdown JSON 代码块的情况。"""

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
        raise RuntimeError("聊天模型返回的 JSON 不是对象")
    return result


def _call_chat_model(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """调用聊天模型，并要求它只在候选列表中选择。"""

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY")

    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    # 统一使用高思考强度；部署环境仍可通过
    # LLM_REASONING_EFFORT 显式覆盖该默认值。
    reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "high")
    candidate_payload = [
        {
            "canonical_symbol": candidate.get("canonical_symbol"),
            "instrument_id": candidate.get("instrument_id"),
            "instrument_type": candidate.get("instrument_type"),
            "name": candidate.get("master_name") or candidate.get("name"),
            "description": candidate.get("master_description") or candidate.get("description"),
            "status": candidate.get("status"),
            "rrf_score": candidate.get("rrf_score"),
            "matched_by": candidate.get("matched_by", []),
        }
        for candidate in candidates
    ]

    system_prompt = """你是受控的金融工具候选筛选器。
你只能从 user_candidates 中选择 instrument_id，绝对不能创造候选列表之外的 ID。
根据用户原始问题、canonical_symbol、名称和描述判断最匹配的工具。
只有一个候选明显匹配时才 decision=select；信息不足或多个候选同样合理时使用 needs_confirmation。
不要给用户没有说出的条件增加额外限定；例如用户只说“美国 CPI”时，不能仅因为候选
名称包含“Core CPI”就擅自把查询解释成核心 CPI，除非用户明确说了“核心”。
返回严格 JSON，不要返回 Markdown，不要添加额外字段：
{"decision":"select|needs_confirmation|not_found","instrument_id":"string|null","confidence":0,"reason":"string"}
"""
    user_payload = {
        "user_query": query,
        "user_candidates": candidate_payload,
    }
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
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
        raise RuntimeError(f"候选筛选模型调用失败：{exc}") from exc

    return _parse_json_content(_extract_message_content(response_body))


def validate_model_selection(model_result: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """校验模型选择，只允许 active 候选继续进入后续查询。"""

    candidate_by_id = {
        candidate["instrument_id"]: candidate
        for candidate in candidates
        if candidate.get("instrument_id") and candidate.get("status", "").lower() == "active"
    }
    decision = model_result.get("decision")
    instrument_id = model_result.get("instrument_id")

    if decision == "select" and instrument_id in candidate_by_id:
        selected = candidate_by_id[instrument_id]
        return {
            "decision": "select",
            "instrument_id": instrument_id,
            "canonical_symbol": selected["canonical_symbol"],
            "confidence": model_result.get("confidence"),
            "reason": model_result.get("reason", ""),
            "candidate": selected,
        }

    if decision in {"needs_confirmation", "not_found"}:
        return {
            "decision": decision,
            "instrument_id": None,
            "canonical_symbol": None,
            "confidence": model_result.get("confidence"),
            "reason": model_result.get("reason", ""),
            "candidate": None,
        }

    raise RuntimeError("聊天模型返回了不在候选列表中的 instrument_id 或无效 decision")


def select_instrument_candidate(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """调用模型并返回经过程序校验的最终候选。"""

    if not candidates:
        return {
            "decision": "not_found",
            "instrument_id": None,
            "canonical_symbol": None,
            "confidence": 0,
            "reason": "没有可供筛选的金融工具候选",
            "candidate": None,
        }
    return validate_model_selection(_call_chat_model(query, candidates), candidates)
