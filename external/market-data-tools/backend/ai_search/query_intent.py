"""兼容旧导入路径的查询意图接口。

实际解析逻辑已经集中到 ``query_parser``，因为最新价格和历史行情必须共享同一
套“路线识别 + 查询主体提取”逻辑。本模块保留旧函数名，避免现有脚本和测试的
导入路径突然失效。
"""

from __future__ import annotations

from typing import Any

from .query_parser import parse_user_query, validate_query_parse_result


def validate_query_intent_result(model_result: dict[str, Any]) -> dict[str, Any]:
    """兼容旧名称；现在同时校验路线和查询主体字段。"""

    return validate_query_parse_result(model_result)


def recognize_query_intent(query: str) -> dict[str, Any]:
    """兼容旧名称；实际返回完整的结构化查询解析结果。"""

    return parse_user_query(query)
