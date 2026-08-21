"""可移植项目的统一配置入口。

所有数据库和模型配置都从 tools/.env 或系统环境变量读取。系统环境变量的优先级
高于 .env，便于部署到云服务器时只替换运行环境配置，不修改查询代码。
"""

from __future__ import annotations

import os
from typing import Any

from .env_config import load_project_env


load_project_env()


def embedding_settings() -> dict[str, Any]:
    """返回统一的 Embedding 配置。

    查询向量和离线文档向量必须来自同一个模型、同一个接口和同一个维度，
    因此所有调用方都从这里读取配置，避免某个检索模块继续使用旧的供应商配置。
    """

    api_key = os.getenv("EMBEDDING_API_KEY")
    if not api_key:
        raise RuntimeError("请在 tools/.env 中配置 EMBEDDING_API_KEY")

    base_url = os.getenv("EMBEDDING_BASE_URL", "").rstrip("/")
    endpoint = os.getenv("EMBEDDING_ENDPOINT", "").strip()
    if base_url:
        endpoint = f"{base_url}/embeddings"
    elif not endpoint:
        endpoint = "https://api.siliconflow.cn/v1/embeddings"
    model = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B")
    dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", "2048"))
    if dimensions < 1:
        raise RuntimeError("EMBEDDING_DIMENSIONS 必须是正整数")
    return {
        "api_key": api_key,
        "endpoint": endpoint,
        "model": model,
        "dimensions": dimensions,
    }


def database_connection_kwargs() -> dict[str, Any]:
    """返回只供后端使用的 PostgreSQL 连接参数，不向前端暴露密码。"""

    password = os.getenv("AI_SEARCH_DB_PASSWORD") or os.getenv("LOCAL_PG_PASSWORD")
    if not password:
        raise RuntimeError("请在 tools/.env 中配置 AI_SEARCH_DB_PASSWORD")
    return {
        "host": os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("AI_SEARCH_DB_PORT", "15433")),
        "dbname": os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"),
        "user": os.getenv("AI_SEARCH_DB_USER", "icbc_collab"),
        "password": password,
        "connect_timeout": int(os.getenv("AI_SEARCH_DB_CONNECT_TIMEOUT", "10")),
    }


def configuration_status() -> dict[str, Any]:
    """返回健康检查所需的配置状态，绝不返回任何密钥值。"""

    return {
        "embedding_configured": bool(os.getenv("EMBEDDING_API_KEY")),
        "candidate_llm_configured": bool(os.getenv("LLM_API_KEY")),
        "database_configured": bool(
            os.getenv("AI_SEARCH_DB_PASSWORD") or os.getenv("LOCAL_PG_PASSWORD")
        ),
        "embedding_model": os.getenv(
            "EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"
        ),
        "embedding_dimensions": int(os.getenv("EMBEDDING_DIMENSIONS", "2048")),
        "candidate_llm_model": os.getenv("LLM_MODEL", ""),
    }
