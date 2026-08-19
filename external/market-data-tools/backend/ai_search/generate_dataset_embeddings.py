"""为 ai_search.dataset_search_documents 生成 Embedding 向量。

该脚本与金融工具 Embedding 生成脚本独立，仅读取和更新数据集目录 AI 表，不会
修改 ai_search.instrument_search_documents。数据集向量文本使用 dataset_catalog
的目录语义字段，不把物理表名作为语义判断依据；最终物理表仍以正式 source 目录
回查结果为准。
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import psycopg2

from .config import embedding_settings
from .env_config import load_project_env
from .generate_embeddings import request_embeddings


# 统一从项目根目录 .env 读取数据库和 Embedding 配置。
load_project_env()


def parse_args() -> argparse.Namespace:
    """读取数据库连接和批量参数。"""

    parser = argparse.ArgumentParser(description="为数据集目录 AI 表生成 Embedding")
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
    """构造数据库连接配置，密码只从环境变量读取。"""

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


def dataset_embedding_text(row: tuple[Any, ...]) -> str:
    """把一条数据集目录转换为 Embedding 输入文本。

    路线选择关注数据集的业务语义，因此使用名称、类型、描述、频率和分类。
    `dataset_id`、`provider`、`access_method` 和 `storage_table_name` 不参与语义
    向量：编号使用精确/模糊匹配，供应商使用可选过滤，技术访问方式和物理表名
    不代表用户意图。
    """

    (
        _dataset_id,
        dataset_name,
        dataset_type,
        _provider,
        description,
        frequency,
        data_category,
        _access_method,
    ) = row
    values = [
        ("dataset_name", dataset_name),
        ("dataset_type", dataset_type),
        ("description", description),
        ("frequency", frequency),
        ("data_category", data_category),
    ]
    return " ".join(f"{name}: {value}" for name, value in values if value)


def main() -> int:
    """读取没有向量的数据集目录，批量调用 Embedding API 并回写数据库。"""

    args = parse_args()
    if args.batch_size < 1:
        raise ValueError("batch-size 必须大于 0")

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
                       dataset_id,
                       dataset_name,
                       dataset_type,
                       provider,
                       description,
                       frequency,
                       data_category,
                       access_method
                FROM ai_search.dataset_search_documents
                {embedding_filter}
                ORDER BY document_id
                """
            )
            documents = cursor.fetchall()

            cursor.execute(
                """
                SELECT udt_name
                FROM information_schema.columns
                WHERE table_schema = 'ai_search'
                  AND table_name = 'dataset_search_documents'
                  AND column_name = 'embedding'
                """
            )
            storage_type_row = cursor.fetchone()
            if storage_type_row is None:
                raise RuntimeError("dataset_search_documents.embedding 列不存在")
            embedding_storage_type = storage_type_row[0]

            completed = 0
            for start in range(0, len(documents), args.batch_size):
                batch = documents[start : start + args.batch_size]
                vectors = request_embeddings(
                    [dataset_embedding_text(row[1:]) for row in batch],
                    api_key,
                    model,
                    endpoint,
                    dimensions,
                )
                for (document_id, *_), vector in zip(batch, vectors):
                    vector_text = "[" + ",".join(str(value) for value in vector) + "]"
                    if embedding_storage_type == "halfvec":
                        cursor.execute(
                            """
                            UPDATE ai_search.dataset_search_documents
                            SET embedding = %s::halfvec
                            WHERE document_id = %s
                            """,
                            (vector_text, document_id),
                        )
                    else:
                        cursor.execute(
                            """
                            UPDATE ai_search.dataset_search_documents
                            SET embedding = %s::jsonb
                            WHERE document_id = %s
                            """,
                            (json.dumps(vector), document_id),
                        )
                completed += len(batch)
                print(f"dataset embedding progress: {completed}/{len(documents)}")
            # 全部文档和批次成功后再提交，避免生成过程被中断后留下半套数据。
            connection.commit()

    print(f"已生成数据集向量：{completed}/{len(documents)}，模型：{model}，维度：{dimensions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
