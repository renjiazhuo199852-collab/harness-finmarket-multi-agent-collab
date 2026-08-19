"""在线检索 ``source.dataset_catalog`` 对应的数据集目录。

本模块是 ``latest_prices`` 路线中独立的数据集阶段，和金融工具检索表完全分开：

1. 从 ``ai_search.dataset_search_documents`` 做精确、关键词、``pg_trgm`` 和
   Embedding 四路召回；
2. 用 ``dataset_id`` 做 RRF 合并和业务去重；
3. 拿候选 ``dataset_id`` 回查 ``source.dataset_catalog``，取得正式目录记录；
4. 在用户指定或前序标识确定供应商时校验 ``provider``；
5. 把最多三个正式候选交给受控大模型，只允许模型选择候选中的 ``dataset_id``；
6. 最终从正式目录记录返回 ``storage_table_name``，绝不让模型生成表名或 SQL。

当前模块只负责确定数据集，不读取 ``dataset_field_catalog``，也不读取四张业务表。
"""

from __future__ import annotations

import argparse
import json
import math
import os
from typing import Any, Callable

import psycopg2

from .dataset_candidate_selector import (
    DatasetCandidateValidationError,
    select_dataset_candidate,
)
from .config import embedding_settings
from .env_config import load_project_env


# 所有在线脚本都从项目根目录加载模型配置；密钥不会进入前端或检索结果。
load_project_env()


RRF_K = 60
DEFAULT_LIMIT = 3
TRIGRAM_THRESHOLD = 0.1
TraceCallback = Callable[[dict[str, Any]], None]


def _dataset_rows_for_trace(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """将数据库行转换为前端可展示的候选摘要，不返回向量和连接信息。"""

    return [
        {
            "document_id": int(row[0]),
            "dataset_id": row[1],
            "dataset_name": row[2],
            "dataset_type": row[3],
            "provider": row[4],
            "description": row[5],
            "frequency": row[6],
            "data_category": row[7],
            "score": round(float(row[8]), 6),
        }
        for row in rows
    ]


def _embedding_result_for_trace(
    result: tuple[list[tuple[Any, ...]], str | None],
) -> dict[str, Any]:
    """整理 Embedding 检索结果和可解释的降级警告。"""

    rows, warning = result
    return {"rows": _dataset_rows_for_trace(rows), "warning": warning}


def _run_traced(
    trace_callback: TraceCallback | None,
    stage: str,
    stage_input: dict[str, Any],
    operation: Callable[[], Any],
    output_serializer: Callable[[Any], Any] = lambda value: value,
) -> Any:
    """执行数据集模块并向前端发送统一的开始、完成或失败事件。"""

    if trace_callback is None:
        return operation()

    import time

    started_at = time.perf_counter()
    trace_callback(
        {
            "stage": stage,
            "status": "running",
            "input": stage_input,
            "output": None,
            "duration_ms": None,
            "error": None,
        }
    )
    try:
        result = operation()
    except Exception as exc:
        trace_callback(
            {
                "stage": stage,
                "status": "error",
                "input": stage_input,
                "output": None,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "error": str(exc),
            }
        )
        raise
    trace_callback(
        {
            "stage": stage,
            "status": "completed",
            "input": stage_input,
            "output": output_serializer(result),
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "error": None,
        }
    )
    return result


def _provider_filter(provider: str | None) -> tuple[str, list[Any]]:
    """生成可选供应商过滤条件。

    用户没有指定供应商时返回空条件；这样不会把 `LSEG` 等值偷偷加入检索请求。
    前序 ``instrument_identifier`` 已经确定供应商时，调用方会把它传入这里，
    从而保证数据集目录和供应商标识属于同一供应商。
    """

    if provider:
        return " AND d.provider = %s", [provider]
    return "", []


def exact_search(
    cursor: Any,
    query: str,
    limit: int,
    provider: str | None = None,
) -> list[tuple[Any, ...]]:
    """按数据集业务请求文本精确匹配 ``dataset_id``，不对用户文本做标准化。"""

    provider_clause, provider_params = _provider_filter(provider)
    sql = f"""
        SELECT document_id, dataset_id, dataset_name, dataset_type, provider,
               description, frequency, data_category, 1.0::double precision AS score
        FROM ai_search.dataset_search_documents AS d
        WHERE d.dataset_id = %s
        {provider_clause}
        ORDER BY document_id
        LIMIT %s
    """
    cursor.execute(sql, tuple([query, *provider_params, limit]))
    return cursor.fetchall()


def keyword_search(
    cursor: Any,
    query: str,
    limit: int,
    provider: str | None = None,
) -> list[tuple[Any, ...]]:
    """用数据集文档的 ``search_vector`` 做全文关键词检索。"""

    provider_clause, provider_params = _provider_filter(provider)
    sql = f"""
        WITH queries AS (
            SELECT websearch_to_tsquery('simple', %s) AS query
        )
        SELECT d.document_id,
               d.dataset_id,
               d.dataset_name,
               d.dataset_type,
               d.provider,
               d.description,
               d.frequency,
               d.data_category,
               ts_rank_cd(d.search_vector, q.query)::double precision AS score
        FROM ai_search.dataset_search_documents AS d
        CROSS JOIN queries AS q
        WHERE d.search_vector @@ q.query
        {provider_clause}
        ORDER BY score DESC, d.document_id
        LIMIT %s
    """
    cursor.execute(sql, tuple([query, *provider_params, limit]))
    return cursor.fetchall()


def trigram_search(
    cursor: Any,
    query: str,
    limit: int,
    provider: str | None = None,
) -> list[tuple[Any, ...]]:
    """用 ``pg_trgm`` 比较数据集编号、名称和业务分类。"""

    provider_clause, provider_params = _provider_filter(provider)
    sql = f"""
        SELECT d.document_id,
               d.dataset_id,
               d.dataset_name,
               d.dataset_type,
               d.provider,
               d.description,
               d.frequency,
               d.data_category,
               GREATEST(
                   similarity(d.dataset_id, %s),
                   similarity(d.dataset_name, %s),
                   similarity(d.data_category, %s)
               )::double precision AS score
        FROM ai_search.dataset_search_documents AS d
        WHERE GREATEST(
                  similarity(d.dataset_id, %s),
                  similarity(d.dataset_name, %s),
                  similarity(d.data_category, %s)
              ) >= %s
        {provider_clause}
        ORDER BY score DESC, d.document_id
        LIMIT %s
    """
    parameters = [query, query, query, query, query, query, TRIGRAM_THRESHOLD]
    parameters.extend(provider_params)
    parameters.append(limit)
    cursor.execute(sql, tuple(parameters))
    return cursor.fetchall()


def embedding_search(
    cursor: Any,
    query: str,
    limit: int,
    provider: str | None = None,
) -> tuple[list[tuple[Any, ...]], str | None]:
    """使用当前配置的 Embedding 模型做数据集业务语义检索。

    如果数据库有 pgvector，则使用 halfvec 的余弦距离；没有 pgvector 时，检索表的
    JSONB 向量会在应用层计算余弦相似度。两种实现对上层输出一致，模型不可用时仅
    跳过这一条召回通道，不影响其他三路检索。
    """

    api_key = os.getenv("EMBEDDING_API_KEY")
    if not api_key:
        return [], "未配置 EMBEDDING_API_KEY，已跳过数据集 Embedding 检索"

    from .generate_embeddings import request_embeddings

    settings = embedding_settings()
    model = settings["model"]
    endpoint = settings["endpoint"]
    dimensions = settings["dimensions"]
    try:
        # 查询原文和上游意图上下文由调用方组成后传入；本模块不修改金融工具代码。
        vector = request_embeddings([query], api_key, model, endpoint, dimensions)[0]
    except Exception as exc:  # noqa: BLE001 - 语义检索失败时必须允许其他通道继续
        return [], f"数据集 Embedding 检索失败，已降级：{exc}"

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
    storage_type = storage_type_row[0] if storage_type_row else None
    vector_text = "[" + ",".join(str(value) for value in vector) + "]"
    provider_clause, provider_params = _provider_filter(provider)

    if storage_type == "halfvec":
        sql = f"""
            SELECT d.document_id,
                   d.dataset_id,
                   d.dataset_name,
                   d.dataset_type,
                   d.provider,
                   d.description,
                   d.frequency,
                   d.data_category,
                   (1.0 - (d.embedding <=> %s::halfvec))::double precision AS score
            FROM ai_search.dataset_search_documents AS d
            WHERE d.embedding IS NOT NULL
            {provider_clause}
            ORDER BY d.embedding <=> %s::halfvec, d.document_id
            LIMIT %s
        """
        parameters = [vector_text, *provider_params, vector_text, limit]
        cursor.execute(sql, tuple(parameters))
        return cursor.fetchall(), None

    # 没有 pgvector 时只读取候选目录和 JSONB 向量，在应用层计算余弦相似度。
    sql = f"""
        SELECT d.document_id,
               d.dataset_id,
               d.dataset_name,
               d.dataset_type,
               d.provider,
               d.description,
               d.frequency,
               d.data_category,
               d.embedding
        FROM ai_search.dataset_search_documents AS d
        WHERE d.embedding IS NOT NULL
        {provider_clause}
        ORDER BY d.document_id
    """
    cursor.execute(sql, tuple(provider_params))
    query_norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    scored_rows: list[tuple[Any, ...]] = []
    for (
        document_id,
        dataset_id,
        dataset_name,
        dataset_type,
        dataset_provider,
        description,
        frequency,
        data_category,
        stored_vector,
    ) in cursor.fetchall():
        if isinstance(stored_vector, str):
            stored_vector = json.loads(stored_vector)
        if not stored_vector or len(stored_vector) != len(vector) or query_norm == 0:
            continue
        stored_norm = math.sqrt(
            sum(float(value) * float(value) for value in stored_vector)
        )
        if stored_norm == 0:
            continue
        score = sum(
            float(left) * float(right)
            for left, right in zip(vector, stored_vector)
        )
        score /= query_norm * stored_norm
        scored_rows.append(
            (
                document_id,
                dataset_id,
                dataset_name,
                dataset_type,
                dataset_provider,
                description,
                frequency,
                data_category,
                float(score),
            )
        )
    scored_rows.sort(key=lambda row: (-row[8], row[0]))
    return (
        scored_rows[:limit],
        "当前数据库未安装 pgvector，数据集 Embedding 使用应用层余弦相似度",
    )


def merge_with_rrf(
    method_results: dict[str, list[tuple[Any, ...]]],
    limit: int,
) -> list[dict[str, Any]]:
    """按 ``dataset_id`` 合并四路结果并保留检索证据。"""

    merged: dict[str, dict[str, Any]] = {}
    for method, rows in method_results.items():
        for rank, row in enumerate(rows, start=1):
            (
                document_id,
                dataset_id,
                dataset_name,
                dataset_type,
                provider,
                description,
                frequency,
                data_category,
                score,
            ) = row
            candidate = merged.setdefault(
                str(dataset_id),
                {
                    "document_id": int(document_id),
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "dataset_type": dataset_type,
                    "provider": provider,
                    "description": description,
                    "frequency": frequency,
                    "data_category": data_category,
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

    return sorted(
        merged.values(),
        key=lambda candidate: (-candidate["rrf_score"], candidate["dataset_id"]),
    )[:limit]


def resolve_dataset_candidates(
    cursor: Any,
    candidates: list[dict[str, Any]],
    provider: str | None = None,
    expected_provider: str | None = None,
    allowed_dataset_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """用候选 ``dataset_id`` 回查正式目录，并校验供应商一致性。

    AI 表中的目录字段只用于召回和排序；这里读取 ``source.dataset_catalog`` 的正式
    记录，因此最终的物理表名来自 source，而不是来自检索文本或大模型。
    """

    if not candidates:
        return [], {
            "resolved": 0,
            "provider_mismatch": 0,
            "not_found": 0,
        }

    dataset_ids = [candidate["dataset_id"] for candidate in candidates]
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
        WHERE dataset_id = ANY(%s)
        """,
        (dataset_ids,),
    )
    catalog_rows = {
        row[0]: {
            "dataset_id": row[0],
            "dataset_name": row[1],
            "dataset_type": row[2],
            "provider": row[3],
            "description": row[4],
            "frequency": row[5],
            "data_category": row[6],
            "access_method": row[7],
            "storage_table_name": row[8],
            "created_at": row[9].isoformat() if row[9] else None,
            "updated_at": row[10].isoformat() if row[10] else None,
        }
        for row in cursor.fetchall()
    }

    # provider 是用户明确输入的过滤条件；expected_provider 是前序有效标识或
    # 后续业务适配器要求的供应商。两者职责不同，不能因为后序查到了 LSEG 就把
    # 它伪装成用户过滤条件。
    provider_to_validate = expected_provider or provider
    allowed_ids = {str(value) for value in (allowed_dataset_ids or set())}
    counts = {"resolved": 0, "provider_mismatch": 0, "not_found": 0}
    if allowed_ids:
        # 只在调用方声明逻辑数据集白名单时增加该计数，兼容旧路线的返回结构。
        counts["unsupported_for_route"] = 0
    resolved_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        resolved = dict(candidate)
        catalog = catalog_rows.get(candidate["dataset_id"])
        if catalog is None:
            resolved.update(
                {
                    "resolution_status": "not_found",
                    "eligible_for_next_step": False,
                    "storage_table_name": None,
                }
            )
            counts["not_found"] += 1
        elif allowed_ids and candidate["dataset_id"] not in allowed_ids:
            resolved.update(
                {
                    **catalog,
                    "resolution_status": "unsupported_for_route",
                    "eligible_for_next_step": False,
                }
            )
            counts["unsupported_for_route"] += 1
        elif provider_to_validate and catalog["provider"] != provider_to_validate:
            resolved.update(
                {
                    **catalog,
                    "resolution_status": "provider_mismatch",
                    "eligible_for_next_step": False,
                }
            )
            counts["provider_mismatch"] += 1
        else:
            resolved.update(
                {
                    **catalog,
                    "resolution_status": "resolved",
                    "eligible_for_next_step": True,
                }
            )
            counts["resolved"] += 1
        resolved_candidates.append(resolved)

    return resolved_candidates, counts


def _dataset_resolution_from_selection(
    model_selection: dict[str, Any] | None,
    reason: str | None = None,
) -> dict[str, Any]:
    """把模型选择统一转换为后续链路使用的数据集确认对象。"""

    if model_selection and model_selection.get("decision") == "select":
        candidate = model_selection.get("candidate") or {}
        return {
            "status": "resolved",
            "dataset_id": candidate.get("dataset_id"),
            "storage_table_name": candidate.get("storage_table_name"),
            "provider": candidate.get("provider"),
            "frequency": candidate.get("frequency"),
            "data_category": candidate.get("data_category"),
            "dataset_name": candidate.get("dataset_name"),
        }
    return {
        "status": (model_selection or {}).get("decision", "skipped"),
        "dataset_id": None,
        "storage_table_name": None,
        "provider": None,
        "frequency": None,
        "data_category": None,
        "dataset_name": None,
        "reason": reason or (model_selection or {}).get("reason", ""),
    }


def search_dataset_documents(
    cursor: Any,
    query: str,
    limit: int = DEFAULT_LIMIT,
    use_embedding: bool = True,
    use_candidate_llm: bool = True,
    provider: str | None = None,
    expected_provider: str | None = None,
    allowed_dataset_ids: set[str] | None = None,
    candidate_selection_query: str | None = None,
    candidate_selection_context: dict[str, Any] | None = None,
    require_candidate_llm: bool = False,
    trace_callback: TraceCallback | None = None,
) -> dict[str, Any]:
    """执行数据集四路检索、正式目录回查和候选模型确认。

    统一入口会设置 ``require_candidate_llm=True``。这样候选筛选模型不可用或被
    请求关闭时，不会静默放行 RRF 第一名，而是返回拒绝状态，阻止后续字段和业务
    表查询。旧的独立路线保留默认值，继续兼容已有测试和调用方式。
    """

    if not query.strip():
        raise ValueError("数据集查询文本不能为空")
    if limit < 1 or limit > 100:
        raise ValueError("数据集 limit 必须在 1 到 100 之间")

    method_results: dict[str, list[tuple[Any, ...]]] = {
        "exact": _run_traced(
            trace_callback,
            "dataset_exact_match",
            {
                "query": query,
                "limit": limit,
                "match_fields": ["dataset_id"],
                "provider": provider,
            },
            lambda: exact_search(cursor, query, limit, provider),
            _dataset_rows_for_trace,
        ),
        "keyword": _run_traced(
            trace_callback,
            "dataset_keyword_search",
            {
                "query": query,
                "limit": limit,
                "search_field": "search_vector",
                "source_fields": [
                    "dataset_name",
                    "data_category",
                    "description",
                    "dataset_type",
                    "frequency",
                ],
                "provider": provider,
            },
            lambda: keyword_search(cursor, query, limit, provider),
            _dataset_rows_for_trace,
        ),
        "pg_trgm": _run_traced(
            trace_callback,
            "dataset_pg_trgm_search",
            {
                "query": query,
                "limit": limit,
                "match_fields": ["dataset_id", "dataset_name", "data_category"],
                "threshold": TRIGRAM_THRESHOLD,
                "provider": provider,
            },
            lambda: trigram_search(cursor, query, limit, provider),
            _dataset_rows_for_trace,
        ),
    }
    warnings: list[str] = []

    if use_embedding:
        embedding_result = _run_traced(
            trace_callback,
            "dataset_embedding_search",
            {
                "query": query,
                "limit": limit,
                "model": os.getenv(
                    "EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"
                ),
                "dimensions": int(os.getenv("EMBEDDING_DIMENSIONS", "2048")),
                "match_fields": [
                    "dataset_name",
                    "data_category",
                    "description",
                    "dataset_type",
                    "frequency",
                ],
                "provider": provider,
            },
            lambda: embedding_search(cursor, query, limit, provider),
            _embedding_result_for_trace,
        )
        embedding_rows, embedding_warning = embedding_result
        if embedding_rows:
            method_results["embedding"] = embedding_rows
        if embedding_warning:
            warnings.append(embedding_warning)
    else:
        _run_traced(
            trace_callback,
            "dataset_embedding_search",
            {"query": query, "enabled": False},
            lambda: ([], "数据集 Embedding 检索已由请求关闭"),
            _embedding_result_for_trace,
        )

    candidates = _run_traced(
        trace_callback,
        "dataset_rrf_merge",
        {
            "limit": limit,
            "methods": {name: len(rows) for name, rows in method_results.items()},
            "deduplication_key": "dataset_id",
        },
        lambda: merge_with_rrf(method_results, limit),
        lambda value: value,
    )
    resolved_candidates, resolution_counts = _run_traced(
        trace_callback,
        "dataset_catalog",
        {
            "candidate_dataset_ids": [candidate["dataset_id"] for candidate in candidates],
            "table": "source.dataset_catalog",
            "provider_requested": provider,
            "provider_required": expected_provider or provider,
            "allowed_dataset_ids": sorted(allowed_dataset_ids or set()),
            "return_fields": [
                "dataset_id",
                "provider",
                "data_category",
                "storage_table_name",
            ],
        },
        lambda: resolve_dataset_candidates(
            cursor,
            candidates,
            provider,
            expected_provider,
            allowed_dataset_ids,
        ),
        lambda value: {"candidates": value[0], "counts": value[1]},
    )

    eligible_candidates = [
        candidate
        for candidate in resolved_candidates
        if candidate.get("eligible_for_next_step")
    ]
    model_selection: dict[str, Any] | None = None
    if use_candidate_llm and not eligible_candidates:
        # 没有通过正式目录或供应商校验的候选时，不再无意义地调用大模型；
        # 统一入口会把这个结果转换为 DATASET_NOT_FOUND，并在字段目录前停止。
        model_selection = _run_traced(
            trace_callback,
            "dataset_candidate_selector",
            {
                "query": candidate_selection_query or query,
                "query_context": candidate_selection_context or {},
                "candidate_count": 0,
                "allowed_dataset_ids": [],
                "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                "skipped_reason": "没有通过正式目录和供应商校验的数据集候选",
            },
            lambda: {
                "decision": "not_found",
                "dataset_id": None,
                "confidence": 0,
                "reason": "没有通过正式目录和供应商校验的数据集候选",
                "candidate": None,
            },
            lambda value: value,
        )
    elif use_candidate_llm:
        try:
            model_selection = _run_traced(
                trace_callback,
                "dataset_candidate_selector",
                {
                    "query": candidate_selection_query or query,
                    "query_context": candidate_selection_context or {},
                    "candidate_count": len(resolved_candidates),
                    "allowed_dataset_ids": [
                        candidate.get("dataset_id")
                        for candidate in resolved_candidates
                        if candidate.get("eligible_for_next_step")
                    ],
                    "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                },
                lambda: select_dataset_candidate(
                    candidate_selection_query or query,
                    resolved_candidates,
                    candidate_selection_context,
                ),
                lambda value: value,
            )
        except DatasetCandidateValidationError as exc:
            # 候选边界校验失败和模型网络不可用不是一回事；前者必须返回明确的
            # DATASET_CANDIDATE_INVALID，不能被包装成可重试的模型不可用。
            warnings.append(f"数据集候选模型返回非法候选，已停止：{exc}")
            model_selection = {
                "decision": "invalid",
                "dataset_id": None,
                "confidence": 0,
                "reason": str(exc),
                "candidate": None,
            }
        except Exception as exc:  # noqa: BLE001 - 模型失败时不允许越权选表
            warnings.append(f"数据集候选模型不可用，已停止最终数据集选择：{exc}")
            model_selection = {
                "decision": "unavailable",
                "dataset_id": None,
                "confidence": 0,
                "reason": "数据集候选筛选模型调用或校验失败",
                "candidate": None,
            }
    else:
        _run_traced(
            trace_callback,
            "dataset_candidate_selector",
            {
                "query": candidate_selection_query or query,
                "enabled": False,
                "required": require_candidate_llm,
            },
            lambda: {
                "decision": "unavailable" if require_candidate_llm else "skipped",
                "dataset_id": None,
                "confidence": 0,
                "reason": (
                    "统一入口要求数据集候选模型完成一致性判断"
                    if require_candidate_llm
                    else "数据集候选模型已由请求关闭"
                ),
                "candidate": None,
            },
            lambda value: value,
        )

    # 这是数据集意图与检索结果之间的程序闸门。语义判断由模型完成，但模型
    # 选择的 ID 必须再次证明来自本次目录候选集合；否则不允许进入字段目录。
    candidate_ids = [
        candidate.get("dataset_id")
        for candidate in resolved_candidates
        if candidate.get("eligible_for_next_step")
    ]
    selected_dataset_id = (model_selection or {}).get("dataset_id")
    if model_selection and model_selection.get("decision") == "invalid":
        consistency_check = {
            "status": "rejected",
            "code": "DATASET_CANDIDATE_INVALID",
            "selected_dataset_id": None,
            "candidate_dataset_ids": candidate_ids,
            "reason": model_selection.get("reason", "模型返回了非法数据集候选"),
        }
    elif not candidate_ids and resolution_counts.get("provider_mismatch", 0) > 0:
        consistency_check = {
            "status": "rejected",
            "code": "DATASET_PROVIDER_MISMATCH",
            "selected_dataset_id": None,
            "candidate_dataset_ids": [],
            "reason": "数据集候选的 provider 与请求供应商不一致，已停止",
        }
    elif not candidate_ids:
        consistency_check = {
            "status": "rejected",
            "code": "DATASET_NOT_FOUND",
            "selected_dataset_id": None,
            "candidate_dataset_ids": [],
            "reason": "没有通过正式目录和供应商校验的数据集候选",
        }
    elif model_selection and model_selection.get("decision") == "select":
        consistency_check = {
            "status": "passed" if selected_dataset_id in candidate_ids else "rejected",
            "code": None if selected_dataset_id in candidate_ids else "DATASET_CANDIDATE_INVALID",
            "selected_dataset_id": selected_dataset_id,
            "candidate_dataset_ids": candidate_ids,
            "reason": (
                "模型选择的数据集属于本次检索候选，允许继续"
                if selected_dataset_id in candidate_ids
                else "模型返回的数据集不属于本次检索候选，已停止"
            ),
        }
    elif model_selection and model_selection.get("decision") == "unavailable":
        consistency_check = {
            "status": "rejected",
            "code": "DATASET_MODEL_UNAVAILABLE",
            "selected_dataset_id": None,
            "candidate_dataset_ids": candidate_ids,
            "reason": model_selection.get("reason", "数据集候选模型不可用"),
        }
    elif not resolved_candidates:
        consistency_check = {
            "status": "rejected",
            "code": "DATASET_NOT_FOUND",
            "selected_dataset_id": None,
            "candidate_dataset_ids": [],
            "reason": "没有通过正式目录和供应商校验的数据集候选",
        }
    else:
        consistency_check = {
            "status": "rejected" if require_candidate_llm else "not_evaluated",
            "code": "DATASET_INTENT_MISMATCH" if require_candidate_llm else None,
            "selected_dataset_id": None,
            "candidate_dataset_ids": candidate_ids,
            "reason": (model_selection or {}).get(
                "reason",
                "数据集候选模型没有选择可执行的数据集",
            ),
        }
    consistency_check = _run_traced(
        trace_callback,
        "dataset_consistency_check",
        {
            "selected_dataset_id": selected_dataset_id,
            "candidate_dataset_ids": candidate_ids,
            "selection_required": require_candidate_llm,
        },
        lambda: consistency_check,
        lambda value: value,
    )

    dataset_resolution = _dataset_resolution_from_selection(model_selection)
    if consistency_check.get("status") != "passed" and not (
        consistency_check.get("status") == "not_evaluated"
        and not require_candidate_llm
    ):
        dataset_resolution = _dataset_resolution_from_selection(
            {
                "decision": "rejected",
                "reason": consistency_check.get("reason", "数据集意图校验未通过"),
            }
        )
        dataset_resolution["status"] = "rejected"
        dataset_resolution["code"] = consistency_check.get("code")
    return {
        "query": query,
        "candidate_selection_query": candidate_selection_query,
        "candidate_selection_context": candidate_selection_context or {},
        "provider_requested": provider,
        "provider_expected": expected_provider,
        "methods": {name: len(rows) for name, rows in method_results.items()},
        "warnings": warnings,
        "catalog_resolution": resolution_counts,
        "candidates": resolved_candidates,
        "model_selection": model_selection,
        "consistency_check": consistency_check,
        "dataset_resolution": dataset_resolution,
    }


def parse_args() -> argparse.Namespace:
    """读取数据集检索命令行参数，便于脱离前端单独验证本模块。"""

    parser = argparse.ArgumentParser(description="检索数据集目录候选")
    parser.add_argument("query", nargs="*", default=["最新价格"])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--no-embedding", action="store_true")
    parser.add_argument("--no-candidate-llm", action="store_true")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--host", default=os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AI_SEARCH_DB_PORT", "15433")))
    parser.add_argument("--database", default=os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"))
    parser.add_argument("--user", default=os.getenv("AI_SEARCH_DB_USER", "icbc_collab"))
    return parser.parse_args()


def connection_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """构造数据库连接配置；密码只从环境变量读取。"""

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


def main() -> int:
    """通过只读连接运行数据集目录检索并打印 JSON 结果。"""

    args = parse_args()
    query = " ".join(args.query).strip()
    with psycopg2.connect(**connection_kwargs(args)) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            result = search_dataset_documents(
                cursor,
                query,
                limit=args.limit,
                use_embedding=not args.no_embedding,
                use_candidate_llm=not args.no_candidate_llm,
                provider=args.provider,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
