"""使用当前 Embedding 模型原子重建三张 AI 检索表的向量。

脚本先从数据库读取全部检索文档，并在内存中完成 271 条向量生成和维度校验；
所有外部 API 请求成功后，才在一个数据库事务中更新三张表、重建 HNSW 索引并
执行 ANALYZE。这样 Embedding 服务中途失败时不会提交半套新向量。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psycopg2

# 允许从项目根目录或 scripts 子目录直接运行本文件，保证 tools 整体复制后仍可用。
TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from backend.ai_search.config import database_connection_kwargs, embedding_settings
from backend.ai_search.generate_dataset_embeddings import dataset_embedding_text
from backend.ai_search.generate_embeddings import request_embeddings
from backend.ai_search.generate_news_embeddings import news_embedding_text


# 三张检索表和对应索引名均为代码内受控常量，绝不接受用户输入作为 SQL 标识符。
TABLES = (
    "instrument_search_documents",
    "dataset_search_documents",
    "news_search_documents",
)
HNSW_INDEXES = (
    "idx_instrument_search_documents_embedding_hnsw",
    "idx_dataset_search_documents_embedding_hnsw",
    "idx_news_search_documents_embedding_hnsw",
)
EXPECTED_ROW_COUNTS = {
    "instrument_search_documents": 188,
    # INSTRUMENT_MASTER 作为目录数据集登记后，数据集检索文档总数为 8。
    "dataset_search_documents": 8,
    "news_search_documents": 76,
}


def parse_args() -> argparse.Namespace:
    """读取批处理大小；数据库和模型配置统一来自 tools/.env。"""

    parser = argparse.ArgumentParser(description="全量重建三张 AI 检索表的 Embedding")
    parser.add_argument("--batch-size", type=int, default=8)
    return parser.parse_args()


def _format_type(cursor: Any, table_name: str) -> str:
    """读取数据库实际向量类型，确认它是 halfvec(2048)。"""

    cursor.execute(
        """
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute AS a
        JOIN pg_class AS c ON c.oid = a.attrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'ai_search'
          AND c.relname = %s
          AND a.attname = 'embedding'
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
        (table_name,),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"ai_search.{table_name}.embedding 列不存在")
    return str(row[0])


def _validate_database_shape(cursor: Any) -> None:
    """在任何外部调用和数据库写入前检查表、向量和索引结构。"""

    for table_name in TABLES:
        actual_type = _format_type(cursor, table_name)
        if actual_type != "halfvec(2048)":
            raise RuntimeError(
                f"ai_search.{table_name}.embedding 类型为 {actual_type}，"
                "预期 halfvec(2048)，已停止迁移"
            )

        cursor.execute(
            f"""
            SELECT count(*)::int,
                   count(embedding)::int,
                   count(*) - count(embedding)
            FROM ai_search.{table_name}
            """
        )
        total, non_null, null_count = cursor.fetchone()
        if total != EXPECTED_ROW_COUNTS[table_name]:
            raise RuntimeError(
                f"ai_search.{table_name} 行数为 {total}，预期 {EXPECTED_ROW_COUNTS[table_name]}，"
                "已停止迁移，请先确认服务器数据是否发生变化"
            )
        print(f"{table_name}: rows={total}, non_null={non_null}, null={null_count}, type={actual_type}")

    cursor.execute(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'ai_search'
          AND indexname = ANY(%s)
        """,
        (list(HNSW_INDEXES),),
    )
    indexes = {name: definition for name, definition in cursor.fetchall()}
    for index_name in HNSW_INDEXES:
        definition = indexes.get(index_name)
        if definition is None or "halfvec_cosine_ops" not in definition:
            raise RuntimeError(f"缺少或配置错误的 HNSW 索引：ai_search.{index_name}")
    print(f"HNSW indexes verified: {len(indexes)}/{len(HNSW_INDEXES)}")


def _fetch_documents(cursor: Any) -> dict[str, list[tuple[int, str]]]:
    """读取三张 AI 检索表的全部文档和向量输入文本。"""

    cursor.execute(
        """
        SELECT document_id,
               concat_ws(' ', NULLIF(canonical_symbol, ''), name, NULLIF(description, ''))
        FROM ai_search.instrument_search_documents
        ORDER BY document_id
        """
    )
    instruments = [(int(document_id), text or "") for document_id, text in cursor.fetchall()]

    cursor.execute(
        """
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
        ORDER BY document_id
        """
    )
    datasets = [
        (int(row[0]), dataset_embedding_text(row[1:]))
        for row in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT document_id,
               title,
               summary,
               content_text,
               related_entities,
               keywords
        FROM ai_search.news_search_documents
        ORDER BY document_id
        """
    )
    news = [(int(row[0]), news_embedding_text((row[0], *row[1:]))) for row in cursor.fetchall()]
    return {
        "instrument_search_documents": instruments,
        "dataset_search_documents": datasets,
        "news_search_documents": news,
    }


def _batched(items: list[tuple[int, str]], size: int) -> Iterable[list[tuple[int, str]]]:
    """按固定大小切分文档，控制单次 Embedding 请求体大小。"""

    for start in range(0, len(items), size):
        yield items[start : start + size]


def _generate_all_vectors(
    documents: dict[str, list[tuple[int, str]]],
    batch_size: int,
    settings: dict[str, Any],
) -> dict[str, list[tuple[int, str]]]:
    """生成并校验全部向量；返回值中的文本字段保存向量字符串。"""

    generated: dict[str, list[tuple[int, str]]] = {}
    total = sum(len(rows) for rows in documents.values())
    completed = 0
    for table_name, rows in documents.items():
        table_vectors: list[tuple[int, str]] = []
        for batch in _batched(rows, batch_size):
            vectors = request_embeddings(
                [text for _, text in batch],
                settings["api_key"],
                settings["model"],
                settings["endpoint"],
                settings["dimensions"],
            )
            for (document_id, _text), vector in zip(batch, vectors):
                if len(vector) != settings["dimensions"]:
                    raise RuntimeError(
                        f"{table_name} document_id={document_id} 返回维度 {len(vector)}，"
                        f"预期 {settings['dimensions']}"
                    )
                vector_text = "[" + ",".join(str(value) for value in vector) + "]"
                table_vectors.append((document_id, vector_text))
            completed += len(batch)
            print(f"embedding progress: {completed}/{total}")
        generated[table_name] = table_vectors
    return generated


def _write_vectors_and_rebuild_indexes(
    connection: Any,
    generated: dict[str, list[tuple[int, str]]],
) -> None:
    """在单个事务中更新向量、重建索引并刷新统计信息。"""

    with connection.cursor() as cursor:
        # API 阶段已全部成功，此处短暂锁表，防止在线查询读到混合模型向量。
        for table_name in TABLES:
            cursor.execute(f"LOCK TABLE ai_search.{table_name} IN ACCESS EXCLUSIVE MODE")

        for table_name in TABLES:
            for document_id, vector_text in generated[table_name]:
                cursor.execute(
                    f"""
                    UPDATE ai_search.{table_name}
                    SET embedding = %s::halfvec
                    WHERE document_id = %s
                    """,
                    (vector_text, document_id),
                )

        for index_name in HNSW_INDEXES:
            cursor.execute(f"REINDEX INDEX ai_search.{index_name}")
        for table_name in TABLES:
            cursor.execute(f"ANALYZE ai_search.{table_name}")
    connection.commit()


def main() -> int:
    """执行结构检查、全量生成、原子更新和最终验收。"""

    args = parse_args()
    if args.batch_size < 1:
        raise ValueError("batch-size 必须大于 0")
    settings = embedding_settings()
    if settings["dimensions"] != 2048:
        raise RuntimeError("本次迁移要求 EMBEDDING_DIMENSIONS=2048")

    with psycopg2.connect(**database_connection_kwargs()) as connection:
        with connection.cursor() as cursor:
            _validate_database_shape(cursor)
            documents = _fetch_documents(cursor)
        # 提交只读检查事务，再开始外部 API 阶段；此时数据库尚未被修改。
        connection.commit()

        generated = _generate_all_vectors(documents, args.batch_size, settings)
        _write_vectors_and_rebuild_indexes(connection, generated)

        with connection.cursor() as cursor:
            _validate_database_shape(cursor)
            for table_name in TABLES:
                cursor.execute(
                    f"SELECT vector_dims(embedding::vector) FROM ai_search.{table_name} LIMIT 1"
                )
                dimension = cursor.fetchone()[0]
                if dimension != 2048:
                    raise RuntimeError(f"{table_name} 写入后维度为 {dimension}，预期 2048")
    print("三张检索表已完成 SiliconFlow 2048 维 Embedding 重建和 HNSW 重建")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
