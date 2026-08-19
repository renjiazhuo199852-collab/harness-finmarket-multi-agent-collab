"""从 ``source.news_articles`` 构建新闻文章 AI 检索文档。

本脚本只处理新闻索引，不修改 ``source.news_articles``，也不生成金融工具关系。
每篇源文章按 ``article_id`` 生成一条检索文档；标题和摘要保持原文，正文只在 AI
索引中清洗 HTML，方便全文、模糊和语义检索共同使用。Embedding 由独立脚本生成。

运行示例：

    python scripts/build_news_search_documents.py
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from html.parser import HTMLParser
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json

from .env_config import load_project_env


# 所有离线任务统一从项目根目录读取 .env，数据库密码不会进入源码。
load_project_env()


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = PROJECT_ROOT / "sql" / "003_create_news_search_documents.sql"


class _HtmlTextParser(HTMLParser):
    """把新闻正文 HTML 转换成可检索的纯文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_html(value: Any) -> str:
    """清洗正文中的标签和多余空白，保留源表中的原始 HTML 不变。"""

    if value is None:
        return ""
    parser = _HtmlTextParser()
    parser.feed(str(value))
    parser.close()
    return " ".join("".join(parser.parts).split())


def text_value(value: Any) -> str:
    """把目录空值转换为空字符串，保证索引文本稳定。"""

    return "" if value is None else str(value).strip()


def json_value(value: Any) -> dict[str, Any]:
    """把源表 JSONB 值转换成可稳定写回检索表的对象。

    新闻快照中的 ``related_entities`` 和 ``keywords`` 是 JSONB，但不同来源可能
    给出 NULL、对象或数组。检索表只要求它们可以被序列化并参与文本检索，因此
    对异常形状统一保留为 ``{"value": ...}``，不让离线索引任务因一条脏数据中断。
    """

    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    return {"value": str(value)}


def parse_args() -> argparse.Namespace:
    """读取数据库连接参数。"""

    parser = argparse.ArgumentParser(description="构建新闻文章 AI 检索文档")
    parser.add_argument("--host", default=os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AI_SEARCH_DB_PORT", "15433")))
    parser.add_argument("--database", default=os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"))
    parser.add_argument("--user", default=os.getenv("AI_SEARCH_DB_USER", "icbc_collab"))
    return parser.parse_args()


def connection_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """构造数据库连接配置；密码只允许来自环境变量。"""

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


def ensure_database_objects(cursor: Any) -> None:
    """执行建表 SQL，确保索引表和检索索引已经存在。"""

    cursor.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def news_documents(cursor: Any) -> Iterable[tuple[Any, ...]]:
    """读取新闻源表，保留候选检索和源表回查所需的全部字段。

    ``source.news_articles`` 的自然键是 ``article_id + source``，不能只用
    ``article_id``。源表的 ``id`` 同时作为回查的技术定位值保存到 AI 表。
    """

    cursor.execute(
        """
        SELECT id,
               article_id,
               source,
               publish_time,
               title,
               language,
               sentiment_score,
               related_entities,
               keywords,
               updated_at,
               content,
               summary
        FROM source.news_articles
        WHERE article_id IS NOT NULL
          AND source IS NOT NULL
        ORDER BY id
        """
    )
    for (
        source_row_id,
        article_id,
        source,
        publish_time,
        title,
        language,
        sentiment_score,
        related_entities,
        keywords,
        updated_at,
        content,
        summary,
    ) in cursor.fetchall():
        yield (
            source_row_id,
            text_value(article_id),
            text_value(source),
            publish_time,
            text_value(title),
            text_value(language),
            sentiment_score,
            json_value(related_entities),
            json_value(keywords),
            updated_at,
            clean_html(content),
            text_value(summary),
        )


def insert_news_document(cursor: Any, document: tuple[Any, ...]) -> None:
    """写入新闻检索文档，并构造四路检索共用的派生字段。

    关键词检索的 ``tsvector`` 只保存 PostgreSQL 倒排索引，不是 Embedding 向量；
    Embedding 由独立脚本生成，避免一次离线导入失败时重复调用外部模型。
    """

    (
        source_row_id,
        article_id,
        source,
        publish_time,
        title,
        language,
        sentiment_score,
        related_entities,
        keywords,
        source_updated_at,
        content_text,
        summary,
    ) = document
    related_entities_text = json.dumps(related_entities, ensure_ascii=False, sort_keys=True)
    keywords_text = json.dumps(keywords, ensure_ascii=False, sort_keys=True)
    cursor.execute(
        """
        INSERT INTO ai_search.news_search_documents (
            source_row_id,
            article_id,
            source,
            publish_time,
            title,
            summary,
            content_text,
            language,
            sentiment_score,
            related_entities,
            keywords,
            source_updated_at,
            search_vector
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'A') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'B') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'B') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'B') ||
            setweight(to_tsvector('simple', COALESCE(%s::text, '')), 'C')
        )
        """,
        (
            source_row_id,
            article_id,
            source,
            publish_time,
            title,
            summary,
            content_text,
            language,
            sentiment_score,
            Json(related_entities),
            Json(keywords),
            source_updated_at,
            title,
            summary,
            related_entities_text,
            keywords_text,
            content_text,
        ),
    )


def rebuild_documents(cursor: Any) -> int:
    """重建新闻索引表；不触碰任何 source 表。"""

    cursor.execute("TRUNCATE TABLE ai_search.news_search_documents RESTART IDENTITY")
    count = 0
    for document in news_documents(cursor):
        insert_news_document(cursor, document)
        count += 1
    return count


def main() -> int:
    """创建新闻检索表并同步当前 source 新闻快照。"""

    args = parse_args()
    with psycopg2.connect(**connection_kwargs(args)) as connection:
        with connection.cursor() as cursor:
            ensure_database_objects(cursor)
            count = rebuild_documents(cursor)
        connection.commit()

    print(f"已生成新闻检索文档：{count}")
    print("source.news_articles 未被修改")
    print("embedding 当前保持为空，后续可运行 generate_news_embeddings.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
