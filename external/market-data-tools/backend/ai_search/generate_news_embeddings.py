"""为 ``ai_search.news_search_documents`` 生成 Embedding 向量。

新闻索引的向量生成与金融工具、数据集目录分开执行。标题、摘要和清洗后的正文
共同表达一篇新闻的语义；向量只用于召回候选，不会改变 source 新闻数据。
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


# 统一从项目根目录读取向量模型配置。
load_project_env()


MAX_EMBEDDING_TEXT = 12000


def parse_args() -> argparse.Namespace:
    """读取数据库连接和批量参数。"""

    parser = argparse.ArgumentParser(description="为新闻检索文档生成 Embedding")
    parser.add_argument("--host", default=os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AI_SEARCH_DB_PORT", "15433")))
    parser.add_argument("--database", default=os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"))
    parser.add_argument("--user", default=os.getenv("AI_SEARCH_DB_USER", "icbc_collab"))
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--force",
        action="store_true",
        help="重新生成所有文档向量；不传入时只处理 embedding 为空的文档",
    )
    return parser.parse_args()


def connection_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """构造数据库连接配置；密码不写入脚本。"""

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


def news_embedding_text(row: tuple[Any, ...]) -> str:
    """把新闻的标题、摘要、正文和结构化关联信息组合成向量输入文本。

    Embedding 的输入包含新闻事实本身，而不包含用户的 ``EUR/USD`` 查询；
    查询向量会在在线阶段单独生成，两者通过余弦相似度比较语义接近程度。
    """

    _document_id, title, summary, content_text, related_entities, keywords = row
    parts = [
        f"title: {title}" if title else "",
        f"summary: {summary}" if summary else "",
        f"content: {content_text}" if content_text else "",
        f"related_entities: {related_entities}" if related_entities else "",
        f"keywords: {keywords}" if keywords else "",
    ]
    return "\n".join(part for part in parts if part)[:MAX_EMBEDDING_TEXT]


def main() -> int:
    """批量读取新闻文档并回写当前配置模型的向量结果。"""

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
                       title,
                       summary,
                       content_text,
                       related_entities,
                       keywords
                FROM ai_search.news_search_documents
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
                  AND table_name = 'news_search_documents'
                  AND column_name = 'embedding'
                """
            )
            storage_type_row = cursor.fetchone()
            if storage_type_row is None:
                raise RuntimeError("news_search_documents.embedding 列不存在")
            storage_type = storage_type_row[0]

            completed = 0
            for start in range(0, len(documents), args.batch_size):
                batch = documents[start : start + args.batch_size]
                vectors = request_embeddings(
                    [news_embedding_text(row) for row in batch],
                    api_key,
                    model,
                    endpoint,
                    dimensions,
                )
                for (document_id, *_), vector in zip(batch, vectors):
                    vector_text = "[" + ",".join(str(value) for value in vector) + "]"
                    if storage_type == "halfvec":
                        cursor.execute(
                            """
                            UPDATE ai_search.news_search_documents
                            SET embedding = %s::halfvec
                            WHERE document_id = %s
                            """,
                            (vector_text, document_id),
                        )
                    else:
                        cursor.execute(
                            """
                            UPDATE ai_search.news_search_documents
                            SET embedding = %s::jsonb
                            WHERE document_id = %s
                            """,
                            (json.dumps(vector), document_id),
                        )
                completed += len(batch)
                print(f"news embedding progress: {completed}/{len(documents)}")
            # 全部新闻向量生成成功后再提交，避免 API 失败造成部分更新。
            connection.commit()

    print(f"已生成新闻向量：{completed}/{len(documents)}，模型：{model}，维度：{dimensions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
