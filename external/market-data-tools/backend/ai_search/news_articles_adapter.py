"""把新闻候选安全回查到 ``source.news_articles``。

新闻候选来自 AI 检索表，不能直接当作最终业务数据。适配器只接受已经由
``dataset_catalog`` 和 ``dataset_field_catalog`` 确认的新闻数据集与字段，并使用
AI 文档中保存的源表 ``id`` 回查事实表。SQL 的表名和字段集合全部是本模块固定的
受控白名单，不接受用户或大模型传入的任意 SQL 片段。
"""

from __future__ import annotations

from typing import Any


NEWS_DATASET_ID = "LSEG_NEWS"
NEWS_TABLE = "news_articles"
NEWS_FIELDS = ("title", "summary", "content")
NewsLimit = int | None


def _empty_result(status: str, reason: str) -> dict[str, Any]:
    """构造统一的空新闻结果，方便前端区分停止原因。"""

    return {"status": status, "rows": [], "row_count": 0, "reason": reason}


def query_news_articles(
    cursor: Any,
    candidates: list[dict[str, Any]],
    dataset_resolution: dict[str, Any],
    field_resolution: dict[str, Any],
    limit: NewsLimit = None,
    start_date: Any | None = None,
    end_date: Any | None = None,
) -> dict[str, Any]:
    """按新闻候选回查源表，并只返回字段目录登记的业务文本字段。

    ``source.news_articles.id`` 当前是源表主键。即使新闻的业务关系仍然只是文本
    或语义相关，候选文档也能通过这个技术定位值准确地回到源表事实行。
    """

    if limit is not None and (limit < 1 or limit > 100):
        raise ValueError("新闻 limit 必须在 1 到 100 之间")
    if dataset_resolution.get("dataset_id") != NEWS_DATASET_ID:
        return _empty_result("unsupported_dataset", "当前适配器只支持 LSEG_NEWS")
    if dataset_resolution.get("storage_table_name") != NEWS_TABLE:
        return _empty_result("unsupported_table", "LSEG_NEWS 没有指向 news_articles")
    if field_resolution.get("status") != "resolved":
        return _empty_result("field_resolution_failed", "新闻字段目录或物理列校验未通过")

    fields = field_resolution.get("fields") or []
    requested_fields = [str(field.get("field_name", "")).lower() for field in fields]
    if set(requested_fields) != set(NEWS_FIELDS):
        return _empty_result("field_resolution_failed", "新闻查询需要 title、summary、content 三个字段")

    source_row_ids: list[int] = []
    candidate_by_source_id: dict[int, dict[str, Any]] = {}
    # 当前新闻路线不设置最终候选条数上限；只有调用方显式传入整数时，才执行
    # 兼容性的截断。默认值为 None，确保所有 RRF 去重后的相关候选都能回查源表。
    selected_candidates = candidates if limit is None else candidates[:limit]
    for candidate in selected_candidates:
        try:
            source_row_id = int(candidate["source_row_id"])
        except (KeyError, TypeError, ValueError):
            continue
        source_row_ids.append(source_row_id)
        candidate_by_source_id[source_row_id] = candidate
    if not source_row_ids:
        return _empty_result("not_found", "没有可回查 source.news_articles 的新闻候选")

    clauses = ["id = ANY(%s)"]
    parameters: list[Any] = [source_row_ids]
    if start_date is not None:
        clauses.append("publish_time >= %s")
        parameters.append(start_date)
    if end_date is not None:
        clauses.append("publish_time < %s")
        parameters.append(end_date)
    cursor.execute(
        f"""
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
        WHERE {' AND '.join(clauses)}
        """,
        tuple(parameters),
    )
    source_rows = {int(row[0]): row for row in cursor.fetchall()}

    # 物理列名是固定的 source 表白名单；最终返回键仍然以字段目录的 field_name
    # 为准，避免把 id、内部更新时间等元数据误当作业务查询字段。
    physical_values = {
        "title": lambda row: row[4],
        "summary": lambda row: row[11],
        "content": lambda row: row[10],
    }
    output_rows: list[dict[str, Any]] = []
    missing_source_rows: list[int] = []
    for source_row_id in source_row_ids:
        source_row = source_rows.get(source_row_id)
        if source_row is None:
            missing_source_rows.append(source_row_id)
            continue
        candidate = candidate_by_source_id[source_row_id]
        data = {
            field_name: physical_values[field_name](source_row)
            for field_name in requested_fields
        }
        output_rows.append(
            {
                "data": data,
                "metadata": {
                    "id": source_row[0],
                    "article_id": source_row[1],
                    "source": source_row[2],
                    "publish_time": source_row[3],
                    "language": source_row[5],
                    "sentiment_score": source_row[6],
                    "related_entities": source_row[7],
                    "keywords": source_row[8],
                    "updated_at": source_row[9],
                    "matched_by": candidate.get("matched_by", []),
                    "method_scores": candidate.get("method_scores", {}),
                    "rrf_score": candidate.get("rrf_score"),
                },
            }
        )

    result: dict[str, Any] = {
        "status": "resolved" if output_rows else "not_found",
        "dataset_id": dataset_resolution.get("dataset_id"),
        "storage_table_name": dataset_resolution.get("storage_table_name"),
        "fields": fields,
        "filters": {
            "source_row_ids": source_row_ids,
            "start_date": start_date,
            "end_date": end_date,
            "candidate_count": len(selected_candidates),
        },
        "rows": output_rows,
        "row_count": len(output_rows),
    }
    if missing_source_rows:
        result["missing_source_row_ids"] = missing_source_rows
    if not output_rows:
        result["reason"] = "候选文档未能回查到 source.news_articles"
    return result
