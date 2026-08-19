"""从数据集候选中选择最终的 ``dataset_id``。

数据集候选筛选与金融工具候选筛选使用相同的聊天模型配置，但保持模块和数据边界
独立：本模块只接收已经由 ``source.dataset_catalog`` 回查过的候选，模型只能在这些
候选的 ``dataset_id`` 中选择，不能生成 ``storage_table_name``、列名或 SQL。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_CHAT_PATH = "/chat/completions"
REQUEST_TIMEOUT = 90


class DatasetCandidateValidationError(ValueError):
    """模型返回越过本次候选集合边界的 dataset_id 时抛出的专用异常。"""


def _chat_endpoint() -> str:
    """拼接 OpenAI 兼容聊天接口地址。"""

    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    chat_path = os.getenv("LLM_CHAT_COMPLETIONS_PATH", DEFAULT_CHAT_PATH)
    if not chat_path.startswith("/"):
        chat_path = "/" + chat_path
    return base_url + chat_path


def _extract_message_content(response_body: dict[str, Any]) -> str:
    """从兼容 OpenAI 的响应中提取模型文本。"""

    choices = response_body.get("choices") or []
    if not choices:
        raise RuntimeError("数据集候选模型响应缺少 choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    raise RuntimeError("数据集候选模型响应缺少可解析的 message.content")


def _parse_json_content(content: str) -> dict[str, Any]:
    """解析严格 JSON，同时兼容模型偶尔返回的 Markdown JSON 代码块。"""

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
        raise RuntimeError("数据集候选模型返回的 JSON 不是对象")
    return result


def _call_chat_model(
    query: str,
    candidates: list[dict[str, Any]],
    query_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """要求聊天模型只从正式数据集候选中选择一个 ``dataset_id``。

    ``query_context`` 由统一查询理解模块产生，作用是补充主体、供应商、时间和
    检索改写。模型仍然只能从 ``candidates`` 中选择，不能依据上下文自行创造
    一个未被目录检索召回的数据集。
    """

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY")

    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    # 数据集候选只需在已回查的少量候选中做受控选择，使用中等思考强度即可，
    # 同时保留环境变量覆盖能力，便于不同部署环境调整模型行为。
    reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "high")
    # 只把候选的业务目录信息交给模型；物理表名刻意不放入候选判断输入，
    # 防止模型把技术实现细节当成业务意图自行推断。
    candidate_payload = [
        {
            "dataset_id": candidate.get("dataset_id"),
            "dataset_name": candidate.get("dataset_name"),
            "dataset_type": candidate.get("dataset_type"),
            "provider": candidate.get("provider"),
            "description": candidate.get("description"),
            "frequency": candidate.get("frequency"),
            "data_category": candidate.get("data_category"),
            "rrf_score": candidate.get("rrf_score"),
            "matched_by": candidate.get("matched_by", []),
        }
        for candidate in candidates
        if candidate.get("eligible_for_next_step")
    ]

    system_prompt = """你是受控的数据集目录候选筛选器。
你只能从 user_candidates 中选择 dataset_id，绝对不能创造候选列表之外的 ID。
根据用户原始问题和候选的数据集名称、分类、描述、频率判断最匹配的数据集。
你不能输出物理表名、字段名、SQL，也不能凭空补充目录信息。
只有一个候选明显匹配时才 decision=select；信息不足或多个候选同样合理时使用 needs_confirmation。
返回严格 JSON，不要返回 Markdown，不要添加额外字段：
{"decision":"select|needs_confirmation|not_found","dataset_id":"string|null","confidence":0,"reason":"string"}
"""
    user_payload = {
        "user_query": query,
        "query_context": query_context or {},
        "user_candidates": candidate_payload,
    }
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
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
        raise RuntimeError(f"数据集候选模型调用失败：{exc}") from exc

    return _parse_json_content(_extract_message_content(response_body))


def validate_dataset_model_selection(
    model_result: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """校验模型输出，只允许选择已回查且供应商约束通过的候选。"""

    candidate_by_id = {
        candidate["dataset_id"]: candidate
        for candidate in candidates
        if candidate.get("dataset_id") and candidate.get("eligible_for_next_step")
    }
    decision = model_result.get("decision")
    dataset_id = model_result.get("dataset_id")

    if decision == "select" and dataset_id in candidate_by_id:
        selected = candidate_by_id[dataset_id]
        return {
            "decision": "select",
            "dataset_id": dataset_id,
            "confidence": model_result.get("confidence"),
            "reason": model_result.get("reason", ""),
            "candidate": selected,
        }

    if decision in {"needs_confirmation", "not_found"} and dataset_id in (None, ""):
        return {
            "decision": decision,
            "dataset_id": None,
            "confidence": model_result.get("confidence"),
            "reason": model_result.get("reason", ""),
            "candidate": None,
        }

    raise DatasetCandidateValidationError(
        "数据集候选模型返回了候选列表之外的 dataset_id、非法 decision，"
        "或在非 select 结果中返回了 dataset_id"
    )


def select_dataset_candidate(
    query: str,
    candidates: list[dict[str, Any]],
    query_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调用模型并立即执行候选边界校验。"""

    return validate_dataset_model_selection(
        _call_chat_model(query, candidates, query_context),
        candidates,
    )
