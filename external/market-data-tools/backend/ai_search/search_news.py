"""检索新闻 AI 文档，返回与用户主体文本或语义相关的新闻候选。

新闻表当前没有可以稳定连接 ``EUR=`` 的 ``source_identifier`` 外键，因此本模块
不把新闻硬连到行情标识。它只在独立的 ``news_search_documents`` 中执行：

1. 精确匹配标题、摘要、正文、关联实体和关键词中的完整文本；
2. 使用 ``search_vector`` 做 PostgreSQL 全文关键词检索；
3. 使用 ``pg_trgm`` 在标题、摘要、关联实体和关键词上做字符相似检索；
4. 使用配置的 Embedding 模型对新闻内容和用户主体做语义检索；
5. 按 ``article_id + source`` 做 RRF 合并和业务去重。

本模块只负责候选召回，不读取 ``source.news_articles`` 的最终事实行；源表回查由
``news_articles_adapter`` 完成。新闻候选不再调用候选筛选大模型，因为用户明确要求
保留多个相关候选，而不是从多个新闻中强行选择一篇。
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Callable

from .config import embedding_settings
from .env_config import load_project_env
from .generate_embeddings import request_embeddings
from .search_instruments import _run_traced


# 在线脚本统一读取项目根目录的 .env，Embedding 密钥不会进入浏览器。
load_project_env()

RRF_K = 60
DEFAULT_TRIGRAM_THRESHOLD = 0.20
DEFAULT_EMBEDDING_MIN_SCORE = 0.40
NewsLimit = int | None
TraceCallback = Callable[[dict[str, Any]], None]


def _news_rows_for_trace(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """把检索行压缩成前端可读的摘要，避免把正文重复推入 SSE。"""

    return [
        {
            "document_id": int(row[0]),
            "source_row_id": row[1],
            "article_id": row[2],
            "source": row[3],
            "publish_time": row[4],
            "title": row[5],
            "score": round(float(row[-1]), 6),
        }
        for row in rows
    ]


def _embedding_result_for_trace(
    result: tuple[list[tuple[Any, ...]], str | None],
) -> dict[str, Any]:
    """整理 Embedding 召回结果和降级警告。"""

    rows, warning = result
    return {"rows": _news_rows_for_trace(rows), "warning": warning}


def _base_select(score_sql: str) -> str:
    """返回四路检索共用的列顺序，避免不同 SQL 的 RRF 行结构不一致。"""

    return f"""
        SELECT document_id,
               source_row_id,
               article_id,
               source,
               publish_time,
               title,
               summary,
               content_text,
               language,
               related_entities,
               keywords,
               {score_sql} AS score
        FROM ai_search.news_search_documents AS d
    """


def _date_filter(
    start_date: Any | None,
    end_date: Any | None,
) -> tuple[str, list[Any]]:
    """生成可选的发布时间条件。

    ``end_date`` 使用半开区间，查询 2026-08-10 时不会漏掉当天有时分秒的新闻，
    同时也不会把下一天的文章误算进来。
    """

    clauses: list[str] = []
    parameters: list[Any] = []
    if start_date is not None:
        clauses.append("d.publish_time >= %s")
        parameters.append(start_date)
    if end_date is not None:
        clauses.append("d.publish_time < %s")
        parameters.append(end_date)
    return (" AND " + " AND ".join(clauses)) if clauses else "", parameters


def _limit_clause(limit: NewsLimit) -> tuple[str, list[int]]:
    """生成新闻检索的可选 LIMIT。

    新闻路线的最终目标是返回全部文本或语义相关候选，因此默认传入 ``None``，
    SQL 不添加 LIMIT。保留整数分支是为了让底层函数可以在单元测试或未来大数据
    分页场景中显式限制召回数量；这个限制不会成为当前新闻路线的默认行为。
    """

    if limit is None:
        return "", []
    return " LIMIT %s", [limit]


def _embedding_min_score() -> float:
    """读取语义相关性门槛，避免无上限检索退化成全表返回。

    取消新闻条数上限后，向量检索仍需要判断“相关”与“仅仅有一个余弦分数”的
    区别。默认 0.40 是当前新闻快照的保守起点；部署环境可以通过环境变量调整，
    但该值只能是 0 到 1 之间的余弦相似度阈值。
    """

    raw_value = os.getenv("NEWS_EMBEDDING_MIN_SCORE", str(DEFAULT_EMBEDDING_MIN_SCORE))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError("NEWS_EMBEDDING_MIN_SCORE 必须是 0 到 1 之间的数字") from exc
    if not 0 <= value <= 1:
        raise ValueError("NEWS_EMBEDDING_MIN_SCORE 必须是 0 到 1 之间的数字")
    return value


def _trigram_threshold() -> float:
    """读取新闻字符模糊匹配门槛，避免短 JSON 片段造成无关召回。

    新闻路线没有固定的工具外键，``pg_trgm`` 只能作为候选召回证据，不能用很低的
    默认门槛把所有字符略有重叠的文章都加入结果。当前快照中正确的外汇干预文章
    分数约为 0.24，而明确无关的采购文章约为 0.11，因此默认从 0.20 起步；保留
    环境变量是为了后续换数据快照时可以重新校准，而不必修改检索代码。
    """

    raw_value = os.getenv("NEWS_TRIGRAM_MIN_SCORE", str(DEFAULT_TRIGRAM_THRESHOLD))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError("NEWS_TRIGRAM_MIN_SCORE 必须是 0 到 1 之间的数字") from exc
    if not 0 <= value <= 1:
        raise ValueError("NEWS_TRIGRAM_MIN_SCORE 必须是 0 到 1 之间的数字")
    return value


def exact_search(
    cursor: Any,
    query: str,
    limit: NewsLimit,
    provider: str | None = None,
    start_date: Any | None = None,
    end_date: Any | None = None,
) -> list[tuple[Any, ...]]:
    """匹配用户主体在新闻文本中的完整连续片段。"""

    provider_clause = " AND d.source = %s" if provider else ""
    date_clause, date_parameters = _date_filter(start_date, end_date)
    limit_clause, limit_parameters = _limit_clause(limit)
    sql = _base_select("1.0::double precision") + f"""
        WHERE position(lower(%s) IN lower(concat_ws(' ', d.title, d.summary,
                     d.content_text, d.related_entities::text, d.keywords::text))) > 0
        {provider_clause}
        {date_clause}
        ORDER BY d.publish_time DESC NULLS LAST, d.document_id
        {limit_clause}
    """
    parameters: list[Any] = [query]
    if provider:
        parameters.append(provider)
    parameters.extend(date_parameters)
    parameters.extend(limit_parameters)
    cursor.execute(sql, tuple(parameters))
    return cursor.fetchall()


def keyword_search(
    cursor: Any,
    query: str,
    limit: NewsLimit,
    provider: str | None = None,
    start_date: Any | None = None,
    end_date: Any | None = None,
) -> list[tuple[Any, ...]]:
    """使用新闻标题、摘要、正文、关联实体和关键词的全文倒排索引。"""

    provider_clause = " AND d.source = %s" if provider else ""
    date_clause, date_parameters = _date_filter(start_date, end_date)
    limit_clause, limit_parameters = _limit_clause(limit)
    sql = _base_select("ts_rank_cd(d.search_vector, q.query)::double precision") + f"""
        CROSS JOIN LATERAL websearch_to_tsquery('simple', %s) AS q(query)
        WHERE d.search_vector @@ q.query
        {provider_clause}
        {date_clause}
        ORDER BY score DESC, d.publish_time DESC NULLS LAST, d.document_id
        {limit_clause}
    """
    parameters: list[Any] = [query]
    if provider:
        parameters.append(provider)
    parameters.extend(date_parameters)
    parameters.extend(limit_parameters)
    cursor.execute(sql, tuple(parameters))
    return cursor.fetchall()


def trigram_search(
    cursor: Any,
    query: str,
    limit: NewsLimit,
    provider: str | None = None,
    start_date: Any | None = None,
    end_date: Any | None = None,
) -> list[tuple[Any, ...]]:
    """用 pg_trgm 查找标题、摘要、关联实体或关键词中的近似文本。"""

    provider_clause = " AND d.source = %s" if provider else ""
    date_clause, date_parameters = _date_filter(start_date, end_date)
    limit_clause, limit_parameters = _limit_clause(limit)
    score = "GREATEST(similarity(d.title, %s), similarity(d.summary, %s), similarity(d.related_entities::text, %s), similarity(d.keywords::text, %s))"
    sql = _base_select(f"{score}::double precision") + f"""
        WHERE GREATEST(
                  similarity(d.title, %s),
                  similarity(d.summary, %s),
                  similarity(d.related_entities::text, %s),
                  similarity(d.keywords::text, %s)
              ) >= %s
        {provider_clause}
        {date_clause}
        ORDER BY score DESC, d.publish_time DESC NULLS LAST, d.document_id
        {limit_clause}
    """
    parameters: list[Any] = [query, query, query, query]
    parameters.extend([query, query, query, query, _trigram_threshold()])
    if provider:
        parameters.append(provider)
    parameters.extend(date_parameters)
    parameters.extend(limit_parameters)
    cursor.execute(sql, tuple(parameters))
    return cursor.fetchall()


def embedding_search(
    cursor: Any,
    query: str,
    limit: NewsLimit,
    provider: str | None = None,
    start_date: Any | None = None,
    end_date: Any | None = None,
) -> tuple[list[tuple[Any, ...]], str | None]:
    """用当前配置的 Embedding 模型召回语义相关新闻；失败时安全降级。"""

    api_key = os.getenv("EMBEDDING_API_KEY")
    if not api_key:
        return [], "未配置 EMBEDDING_API_KEY，已跳过新闻 Embedding 检索"

    settings = embedding_settings()
    model = settings["model"]
    endpoint = settings["endpoint"]
    dimensions = settings["dimensions"]
    try:
        vector = request_embeddings([query], api_key, model, endpoint, dimensions)[0]
    except Exception as exc:  # noqa: BLE001 - 语义通道失败不能阻断其他召回
        return [], f"新闻 Embedding 检索失败，已降级：{exc}"

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
    storage_type = storage_type_row[0] if storage_type_row else None
    if storage_type is None:
        return [], "news_search_documents.embedding 列不存在，已跳过语义检索"

    vector_text = "[" + ",".join(str(value) for value in vector) + "]"
    min_score = _embedding_min_score()
    provider_clause = " AND d.source = %s" if provider else ""
    date_clause, date_parameters = _date_filter(start_date, end_date)
    limit_clause, limit_parameters = _limit_clause(limit)
    if storage_type == "halfvec":
        sql = _base_select("(1.0 - (d.embedding <=> %s::halfvec))::double precision") + f"""
            WHERE d.embedding IS NOT NULL
              AND (1.0 - (d.embedding <=> %s::halfvec)) >= %s
            {provider_clause}
            {date_clause}
            ORDER BY d.embedding <=> %s::halfvec, d.publish_time DESC NULLS LAST, d.document_id
            {limit_clause}
        """
        parameters: list[Any] = [vector_text, vector_text, min_score]
        if provider:
            parameters.append(provider)
        parameters.extend(date_parameters)
        parameters.append(vector_text)
        parameters.extend(limit_parameters)
        cursor.execute(sql, tuple(parameters))
        return cursor.fetchall(), None

    # 未安装 pgvector 时读取 JSONB 向量，在应用层计算余弦相似度，保证本地开发环境
    # 仍然可以使用 Embedding 通道，只是不能利用数据库 HNSW 索引。
    sql = """
        SELECT document_id,
               source_row_id,
               article_id,
               source,
               publish_time,
               title,
               summary,
               content_text,
               language,
               related_entities,
               keywords,
               embedding
        FROM ai_search.news_search_documents AS d
        WHERE d.embedding IS NOT NULL
    """
    parameters = []
    if provider:
        sql += " AND d.source = %s"
        parameters.append(provider)
    if start_date is not None:
        sql += " AND d.publish_time >= %s"
        parameters.append(start_date)
    if end_date is not None:
        sql += " AND d.publish_time < %s"
        parameters.append(end_date)
    sql += " ORDER BY d.document_id"
    cursor.execute(sql, tuple(parameters))
    query_norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    scored_rows: list[tuple[Any, ...]] = []
    for row in cursor.fetchall():
        stored_vector = row[-1]
        if isinstance(stored_vector, str):
            stored_vector = json.loads(stored_vector)
        if not stored_vector or len(stored_vector) != len(vector) or query_norm == 0:
            continue
        stored_norm = math.sqrt(sum(float(value) * float(value) for value in stored_vector))
        if stored_norm == 0:
            continue
        score = sum(
            float(left) * float(right)
            for left, right in zip(vector, stored_vector)
        ) / (query_norm * stored_norm)
        if score < min_score:
            continue
        scored_rows.append((*row[:-1], float(score)))
    scored_rows.sort(key=lambda row: (-row[-1], row[0]))
    if limit is not None:
        scored_rows = scored_rows[:limit]
    return scored_rows, "当前数据库未安装 pgvector，新闻 Embedding 使用应用层余弦相似度"


def merge_with_rrf(
    method_results: dict[str, list[tuple[Any, ...]]],
    limit: NewsLimit,
) -> list[dict[str, Any]]:
    """按 ``article_id + source`` 合并四路新闻召回并保留匹配证据。"""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for method, rows in method_results.items():
        for rank, row in enumerate(rows, start=1):
            (
                document_id,
                source_row_id,
                article_id,
                source,
                publish_time,
                title,
                summary,
                _content_text,
                language,
                _related_entities,
                _keywords,
                score,
            ) = row
            business_key = (str(article_id), str(source or ""))
            candidate = merged.setdefault(
                business_key,
                {
                    "document_id": int(document_id),
                    "source_row_id": source_row_id,
                    "article_id": article_id,
                    "source": source,
                    "publish_time": publish_time,
                    "title": title,
                    "summary": summary,
                    "language": language,
                    "matched_by": [],
                    "method_scores": {},
                    "rrf_score": 0.0,
                },
            )
            candidate["rrf_score"] += 1.0 / (RRF_K + rank)
            candidate["matched_by"].append(method)
            candidate["method_scores"][method] = round(float(score), 6)

    for candidate in merged.values():
        candidate["matched_by"] = sorted(set(candidate["matched_by"]))
        candidate["rrf_score"] = round(candidate["rrf_score"], 6)

    ordered_candidates = sorted(
        merged.values(),
        key=lambda candidate: (-candidate["rrf_score"], str(candidate["article_id"])),
    )
    return ordered_candidates if limit is None else ordered_candidates[:limit]


def search_news_documents(
    cursor: Any,
    query: str,
    limit: NewsLimit = None,
    use_embedding: bool = True,
    provider: str | None = None,
    start_date: Any | None = None,
    end_date: Any | None = None,
    trace_callback: TraceCallback | None = None,
) -> dict[str, Any]:
    """执行新闻四路召回和 RRF，返回多个新闻候选而不是单一选择。"""

    clean_query = query.strip()
    if not clean_query:
        raise ValueError("新闻检索文本不能为空")
    if limit is not None and (limit < 1 or limit > 100):
        raise ValueError("新闻 limit 必须在 1 到 100 之间")

    method_results: dict[str, list[tuple[Any, ...]]] = {
        "exact": _run_traced(
            trace_callback,
            "news_exact_match",
            {
                "query": clean_query,
                "limit": limit,
                "match_fields": ["title", "summary", "content_text", "related_entities", "keywords"],
                "provider": provider,
                "start_date": start_date,
                "end_date": end_date,
            },
            lambda: exact_search(cursor, clean_query, limit, provider, start_date, end_date),
            _news_rows_for_trace,
        ),
        "keyword": _run_traced(
            trace_callback,
            "news_keyword_search",
            {
                "query": clean_query,
                "limit": limit,
                "search_field": "search_vector",
                "source_fields": ["title", "summary", "content", "related_entities", "keywords"],
                "provider": provider,
                "start_date": start_date,
                "end_date": end_date,
            },
            lambda: keyword_search(cursor, clean_query, limit, provider, start_date, end_date),
            _news_rows_for_trace,
        ),
        "pg_trgm": _run_traced(
            trace_callback,
            "news_pg_trgm_search",
            {
                "query": clean_query,
                "limit": limit,
                "match_fields": ["title", "summary", "related_entities", "keywords"],
                "threshold": _trigram_threshold(),
                "provider": provider,
                "start_date": start_date,
                "end_date": end_date,
            },
            lambda: trigram_search(cursor, clean_query, limit, provider, start_date, end_date),
            _news_rows_for_trace,
        ),
    }
    warnings: list[str] = []
    if use_embedding:
        embedding_rows, warning = _run_traced(
            trace_callback,
            "news_embedding_search",
            {
                "query": clean_query,
                "limit": limit,
                "min_score": _embedding_min_score(),
                "model": os.getenv(
                    "EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"
                ),
                "dimensions": int(os.getenv("EMBEDDING_DIMENSIONS", "2048")),
                "provider": provider,
                "start_date": start_date,
                "end_date": end_date,
            },
            lambda: embedding_search(cursor, clean_query, limit, provider, start_date, end_date),
            _embedding_result_for_trace,
        )
        if embedding_rows:
            method_results["embedding"] = embedding_rows
        if warning:
            warnings.append(warning)
    else:
        _run_traced(
            trace_callback,
            "news_embedding_search",
            {"query": clean_query, "enabled": False},
            lambda: ([], "新闻 Embedding 检索已由请求关闭"),
            _embedding_result_for_trace,
        )

    candidates = _run_traced(
        trace_callback,
        "news_rrf_merge",
        {
            "limit": limit,
            "methods": {name: len(rows) for name, rows in method_results.items()},
            "deduplication_key": ["article_id", "source"],
        },
        lambda: merge_with_rrf(method_results, limit),
        lambda value: value,
    )
    return {
        "query": clean_query,
        "provider": provider,
        "methods": {name: len(rows) for name, rows in method_results.items()},
        "warnings": warnings,
        "embedding_min_score": _embedding_min_score() if use_embedding else None,
        "candidates": candidates,
        "candidate_selection": None,
    }
