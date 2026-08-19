"""从 source.dataset_catalog 生成独立的数据集 AI 检索表。

本脚本只处理数据集目录，不读取 instrument_master、instrument_identifier 或四张
业务数据表。它会创建 ``ai_search.dataset_search_documents``，复制数据集目录的
正式字段，并生成关键词检索使用的 ``tsvector``。Embedding 留给独立的后处理任务，
与当前金融工具文档的生成方式保持一致。

运行示例：

    $env:AI_SEARCH_DB_PASSWORD = "本地 PostgreSQL 密码"
    python scripts/build_dataset_search_documents.py
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Iterable

import psycopg2

from .env_config import load_project_env


# 所有离线构建脚本使用项目根目录 .env，避免把数据库密码写入命令或源码。
load_project_env()


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = PROJECT_ROOT / "sql" / "002_create_dataset_search_documents.sql"


def parse_args() -> argparse.Namespace:
    """读取数据库连接参数；密码只从环境变量读取。"""

    parser = argparse.ArgumentParser(description="从 source.dataset_catalog 生成数据集检索文档")
    parser.add_argument("--host", default=os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AI_SEARCH_DB_PORT", "15433")))
    parser.add_argument("--database", default=os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"))
    parser.add_argument("--user", default=os.getenv("AI_SEARCH_DB_USER", "icbc_collab"))
    return parser.parse_args()


def connection_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """构造数据库连接配置，优先使用项目专用密码变量。"""

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
    """将目录空值转换为空字符串，避免检索文本中出现 Python 的 None。"""

    return "" if value is None else str(value).strip()


def ensure_database_objects(cursor: Any) -> None:
    """执行独立 SQL，确保数据集 Schema、表和索引已经存在。"""

    cursor.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def dataset_documents(cursor: Any) -> Iterable[tuple[Any, ...]]:
    """读取 source.dataset_catalog 的全部目录行，不与金融工具表做 JOIN。"""

    cursor.execute(
        """
        SELECT dataset_id,
               dataset_name,
               dataset_type,
               provider,
               description,
               frequency,
               data_category,
               access_method,
               storage_table_name,
               created_at,
               updated_at
        FROM source.dataset_catalog
        WHERE dataset_id IS NOT NULL
        ORDER BY dataset_id
        """
    )
    for row in cursor.fetchall():
        yield (
            text_value(row[0]),
            text_value(row[1]),
            text_value(row[2]),
            text_value(row[3]),
            text_value(row[4]),
            text_value(row[5]),
            text_value(row[6]),
            text_value(row[7]),
            text_value(row[8]),
            row[9],
            row[10],
        )


def insert_dataset_document(cursor: Any, document: tuple[Any, ...]) -> None:
    """写入一条数据集目录，并按目录字段权重构造全文检索向量。

    `dataset_id` 权重最高，适合已知目录编号的查询；`dataset_name` 和 `data_category`
    次之，`description`、`dataset_type` 和 `frequency` 用于补充业务语义。`provider`
    是可选的精确过滤条件，不参与全文排名；`access_method` 和物理表名也不参与
    用户意图检索。原始字段仍然单独保存，后续回查 source.dataset_catalog 时不依赖
    搜索文本。
    """

    (
        dataset_id,
        dataset_name,
        dataset_type,
        provider,
        description,
        frequency,
        data_category,
        access_method,
        storage_table_name,
        source_created_at,
        source_updated_at,
    ) = document
    cursor.execute(
        """
        INSERT INTO ai_search.dataset_search_documents (
            dataset_id,
            dataset_name,
            dataset_type,
            provider,
            description,
            frequency,
            data_category,
            access_method,
            storage_table_name,
            source_created_at,
            source_updated_at,
            search_vector
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'A') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'B') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'B') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'C') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'C') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'C')
        )
        """,
        (
            dataset_id,
            dataset_name,
            dataset_type,
            provider,
            description,
            frequency,
            data_category,
            access_method,
            storage_table_name,
            source_created_at,
            source_updated_at,
            dataset_id,
            dataset_name,
            data_category,
            description,
            dataset_type,
            frequency,
        ),
    )


def rebuild_documents(cursor: Any) -> int:
    """只重建数据集目录表，绝不清空 instrument_search_documents。"""

    cursor.execute("TRUNCATE TABLE ai_search.dataset_search_documents RESTART IDENTITY")
    count = 0
    for document in dataset_documents(cursor):
        insert_dataset_document(cursor, document)
        count += 1
    return count


def main() -> int:
    """初始化数据集检索表并从 source 目录生成文档。"""

    args = parse_args()
    with psycopg2.connect(**connection_kwargs(args)) as connection:
        with connection.cursor() as cursor:
            ensure_database_objects(cursor)
            count = rebuild_documents(cursor)
        connection.commit()

    print(f"已生成数据集检索文档：{count}")
    print("instrument_search_documents 未被修改")
    print("embedding 当前保持为空，后续可单独为 dataset_search_documents 生成向量")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
