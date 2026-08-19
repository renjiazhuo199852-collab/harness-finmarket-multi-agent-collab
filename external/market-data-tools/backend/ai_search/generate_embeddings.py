"""为 ai_search.instrument_search_documents 生成 Embedding 向量。

关键词检索、pg_trgm 和表结构初始化不依赖外部模型；本脚本只负责最后一层语义检索。
Embedding 服务使用统一的环境变量配置，未配置 API Key 时不会生成伪造向量。
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

import psycopg2

from .config import embedding_settings
from .env_config import load_project_env


# 统一从项目根目录 .env 读取 Embedding 服务配置。
load_project_env()


EXPECTED_DIMENSION = int(os.getenv("EMBEDDING_DIMENSIONS", "2048"))


def parse_args() -> argparse.Namespace:
    """读取数据库、模型和批量大小配置。"""

    parser = argparse.ArgumentParser(description="为 AI Search 文档生成 Embedding")
    parser.add_argument("--host", default=os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AI_SEARCH_DB_PORT", "15433")))
    parser.add_argument("--database", default=os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"))
    parser.add_argument("--user", default=os.getenv("AI_SEARCH_DB_USER", "icbc_collab"))
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument(
        "--force",
        action="store_true",
        help="重新生成所有文档向量；不传入时只处理 embedding 为空的文档",
    )
    return parser.parse_args()


def connection_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """从环境变量读取数据库密码，避免把密码写入代码。"""

    password = os.getenv("AI_SEARCH_DB_PASSWORD") or os.getenv("LOCAL_PG_PASSWORD")
    if not password:
        raise RuntimeError("请先设置 AI_SEARCH_DB_PASSWORD 或 LOCAL_PG_PASSWORD")
    return {
        "host": args.host,
        "port": args.port,
        "dbname": args.database,
        "user": args.user,
        "password": password,
        "connect_timeout": 10,
    }


def request_embeddings(
    texts: list[str],
    api_key: str,
    model: str,
    endpoint: str,
    dimensions: int = EXPECTED_DIMENSION,
) -> list[list[float]]:
    """批量调用 Embedding API，并校验返回数量和维度。

    Qwen3-Embedding-8B 默认返回 4096 维。当前数据库使用 ``halfvec(2048)``，
    因此必须把目标维度放进请求体，避免在线查询向量和数据库向量维度不一致。
    """

    payload = json.dumps(
        {"model": model, "input": texts, "dimensions": dimensions},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
            data = sorted(body.get("data") or [], key=lambda item: item.get("index", 0))
            vectors = [item.get("embedding") for item in data]
            if len(vectors) != len(texts) or any(
                len(vector or []) != dimensions for vector in vectors
            ):
                raise RuntimeError(
                    f"Embedding 返回数量或维度不正确：count={len(vectors)}, "
                    f"dimension={len(vectors[0]) if vectors else None}, "
                    f"expected_dimension={dimensions}"
                )
            return vectors
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"Embedding API 调用失败：{last_error}") from last_error


def main() -> int:
    """读取没有向量的文档，批量生成向量并写入 halfvec 列。"""

    args = parse_args()
    settings = embedding_settings()
    api_key = settings["api_key"]
    model = settings["model"]
    endpoint = settings["endpoint"]
    dimensions = settings["dimensions"]
    embedding_filter = "" if args.force else "WHERE embedding IS NULL"

    with psycopg2.connect(**connection_kwargs(args)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT document_id,
                       concat_ws(' ', NULLIF(canonical_symbol, ''), name, NULLIF(description, ''))
                FROM ai_search.instrument_search_documents
                {embedding_filter}
                ORDER BY document_id
                """
            )
            documents = cursor.fetchall()
            # 根据初始化脚本最终选择的存储类型写入向量：pgvector 环境写 halfvec，
            # 当前未安装 pgvector 的环境写 JSONB；两者都保持原始 2048 维数据。
            cursor.execute(
                """
                SELECT udt_name
                FROM information_schema.columns
                WHERE table_schema = 'ai_search'
                  AND table_name = 'instrument_search_documents'
                  AND column_name = 'embedding'
                """
            )
            embedding_storage_type = cursor.fetchone()[0]
            completed = 0
            for start in range(0, len(documents), args.batch_size):
                batch = documents[start : start + args.batch_size]
                vectors = request_embeddings(
                    [row[1] for row in batch], api_key, model, endpoint, dimensions
                )
                for (document_id, _text), vector in zip(batch, vectors):
                    vector_text = "[" + ",".join(str(value) for value in vector) + "]"
                    if embedding_storage_type == "halfvec":
                        cursor.execute(
                            """
                            UPDATE ai_search.instrument_search_documents
                            SET embedding = %s::halfvec
                            WHERE document_id = %s
                            """,
                            (vector_text, document_id),
                        )
                    else:
                        cursor.execute(
                            """
                            UPDATE ai_search.instrument_search_documents
                            SET embedding = %s::jsonb
                            WHERE document_id = %s
                            """,
                            (json.dumps(vector), document_id),
                        )
                completed += len(batch)
                print(f"embedding progress: {completed}/{len(documents)}")
            # 只有全部批次成功后才提交，避免 API 中途失败留下半套新向量。
            connection.commit()

    print(f"已生成向量：{completed}/{len(documents)}，模型：{model}，维度：{dimensions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
