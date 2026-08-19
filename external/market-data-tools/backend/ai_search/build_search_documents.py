"""从 source 目录生成 ai_search.instrument_search_documents。

本脚本只读取业务目录，不复制价格、行情、宏观数据或新闻正文。当前阶段只生成
金融工具检索文档；数据集和字段文档留到后续阶段处理。金融工具文档只使用标准
代码、名称和描述，供应商 identifier 仍由在线查询阶段从 source.instrument_identifier
读取。

运行前需要设置数据库密码环境变量：

    $env:AI_SEARCH_DB_PASSWORD = "..."
    python scripts/build_search_documents.py

如果没有设置专用变量，也兼容项目已有的 LOCAL_PG_PASSWORD。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Iterable

import psycopg2

from .env_config import load_project_env


# 允许目录构建脚本使用项目根目录 .env 中的数据库配置。
load_project_env()


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = PROJECT_ROOT / "sql" / "001_create_search_documents.sql"


def parse_args() -> argparse.Namespace:
    """读取数据库连接参数；密码只从环境变量读取，不写入源代码。"""

    parser = argparse.ArgumentParser(description="从 source 目录生成 AI Search 检索文档")
    parser.add_argument("--host", default=os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AI_SEARCH_DB_PORT", "15433")))
    parser.add_argument("--database", default=os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"))
    parser.add_argument("--user", default=os.getenv("AI_SEARCH_DB_USER", "icbc_collab"))
    return parser.parse_args()


def connection_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """构造数据库连接配置，并优先使用新项目专用密码变量。"""

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


def text_value(value: Any) -> str:
    """把目录中的空值统一转换为空字符串，避免把 None 写入检索文本。"""

    return "" if value is None else str(value).strip()


def ensure_database_objects(cursor: Any) -> None:
    """执行独立 SQL 文件，确保 Schema、检索表和四类索引存在。"""

    cursor.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def instrument_documents(cursor: Any) -> Iterable[tuple[str, str, str, str]]:
    """读取所有金融工具目录行；离线阶段不按 status 过滤。"""

    cursor.execute(
        """
        SELECT canonical_symbol, name, description
        FROM source.instrument_master
        WHERE canonical_symbol IS NOT NULL
        ORDER BY canonical_symbol
        """
    )
    for canonical_symbol, name, description in cursor.fetchall():
        yield (
            "instrument",
            text_value(canonical_symbol),
            text_value(name),
            text_value(description),
        )


def insert_document(cursor: Any, document: tuple[str, str, str, str]) -> None:
    """插入一条金融工具文档，并为三个原始文本字段生成加权全文检索向量。

    `canonical_symbol` 权重最高，适合金融工具代码精确相关的关键词命中；名称次之，
    描述权重较低。原始文本同时保留，供精确匹配和 pg_trgm 模糊检索使用。
    """

    document_type, canonical_symbol, name, description = document
    cursor.execute(
        """
        INSERT INTO ai_search.instrument_search_documents (
            document_type,
            canonical_symbol,
            name,
            description,
            search_vector
        )
        VALUES (
            %s, %s, %s, %s,
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'A') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'B') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'C')
        )
        """,
        (
            document_type,
            canonical_symbol,
            name,
            description,
            canonical_symbol,
            name,
            description,
        ),
    )


def rebuild_documents(cursor: Any) -> dict[str, int]:
    """清空并只重建金融工具文档，确保不会残留后续阶段的旧目录文档。"""

    cursor.execute("TRUNCATE TABLE ai_search.instrument_search_documents RESTART IDENTITY")
    counts = {"instrument": 0, "dataset": 0, "field": 0}
    for document in instrument_documents(cursor):
        insert_document(cursor, document)
        counts[document[0]] += 1
    return counts


def main() -> int:
    """初始化检索表并从 source 目录生成可检索文档。"""

    args = parse_args()
    with psycopg2.connect(**connection_kwargs(args)) as connection:
        with connection.cursor() as cursor:
            ensure_database_objects(cursor)
            counts = rebuild_documents(cursor)
        connection.commit()

    total = sum(counts.values())
    print(f"已生成检索文档：{total}")
    print(f"instrument: {counts['instrument']}")
    print(f"dataset: {counts['dataset']}")
    print(f"field: {counts['field']}")
    print("embedding 当前保持为空，需单独运行 Embedding 生成脚本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
