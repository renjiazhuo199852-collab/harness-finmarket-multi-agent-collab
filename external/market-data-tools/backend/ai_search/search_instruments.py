"""检索金融工具检索表，并返回合并后的候选结果。

本脚本实现在线查询链路的第一步：接收用户自然语言，
从 ``ai_search.instrument_search_documents`` 召回金融工具候选。

当前脚本只负责检索候选，不执行以下动作：

* 不查询 ``source.instrument_identifier``；
* 不查询 ``source.latest_prices``；
* 不生成或执行原始 SQL；
* 不由程序直接把候选当作已经确认的 ``instrument_id``。

运行示例：

    $env:AI_SEARCH_DB_PASSWORD = "本地 PostgreSQL 密码"
    python scripts/search_instruments.py EURUSD

如果没有配置 ``EMBEDDING_API_KEY``，程序会跳过 Embedding 检索，
仍然执行精确匹配、关键词检索和 ``pg_trgm`` 模糊检索。调用方应传入已经从用户
问题中提取出的工具主体文本；本脚本不做金融工具符号标准化。
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import math
import os
import time
from typing import Any, Callable

import psycopg2

from .candidate_selector import select_instrument_candidate
from .config import embedding_settings
from .env_config import load_project_env


# 在读取 Embedding 和聊天模型配置之前加载项目本地 .env。
load_project_env()


RRF_K = 60
DEFAULT_LIMIT = 3
TRIGRAM_THRESHOLD = 0.1
TraceCallback = Callable[[dict[str, Any]], None]


def _rows_for_trace(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """把数据库检索行转换为前端可展示的安全摘要，不返回向量或连接信息。"""

    return [
        {
            "document_id": int(row[0]),
            "canonical_symbol": row[1],
            "name": row[2],
            "description": row[3],
            "score": round(float(row[4]), 6),
        }
        for row in rows
    ]


def _embedding_result_for_trace(
    result: tuple[list[tuple[Any, ...]], str | None],
) -> dict[str, Any]:
    """把 Embedding 检索的行和降级警告整理成前端可读的对象。"""

    rows, warning = result
    return {"rows": _rows_for_trace(rows), "warning": warning}


def _run_traced(
    trace_callback: TraceCallback | None,
    stage: str,
    stage_input: dict[str, Any],
    operation: Callable[[], Any],
    output_serializer: Callable[[Any], Any] = lambda value: value,
) -> Any:
    """执行一个模块并发送统一的运行轨迹事件。"""

    if trace_callback is None:
        return operation()

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


def parse_args() -> argparse.Namespace:
    """读取命令行参数；查询文本支持不加引号的多个词。"""

    parser = argparse.ArgumentParser(description="检索金融工具候选")
    parser.add_argument(
        "query",
        nargs="*",
        default=["EURUSD"],
        help="用户自然语言问题，例如 EURUSD 或 查询 EUR/USD 最新价格",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="最终返回候选数量")
    parser.add_argument(
        "--no-embedding",
        action="store_true",
        help="只执行精确、关键词和 pg_trgm 检索，不调用 Embedding API",
    )
    parser.add_argument(
        "--no-candidate-llm",
        action="store_true",
        help="只返回 Top 3 候选，不调用候选筛选聊天模型",
    )
    parser.add_argument("--host", default=os.getenv("AI_SEARCH_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AI_SEARCH_DB_PORT", "15433")))
    parser.add_argument("--database", default=os.getenv("AI_SEARCH_DB_NAME", "icbc_shared"))
    parser.add_argument("--user", default=os.getenv("AI_SEARCH_DB_USER", "icbc_collab"))
    parser.add_argument("--provider", default=None, help="可选供应商，例如 LSEG")
    return parser.parse_args()


def connection_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """构造数据库连接参数；密码只从环境变量读取。"""

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


def exact_search(cursor: Any, query: str, limit: int) -> list[tuple[Any, ...]]:
    """按调用方提供的工具主体文本做字面精确匹配，不修改查询文本。"""

    cursor.execute(
        """
        SELECT document_id, canonical_symbol, name, description, 1.0::double precision AS score
        FROM ai_search.instrument_search_documents
        WHERE canonical_symbol = %s
        ORDER BY document_id
        LIMIT %s
        """,
        (query, limit),
    )
    return cursor.fetchall()


def keyword_search(cursor: Any, query: str, limit: int) -> list[tuple[Any, ...]]:
    """使用工具主体文本在 search_vector 中做全文关键词检索。"""

    cursor.execute(
        """
        WITH queries AS (
            SELECT websearch_to_tsquery('simple', %s) AS query
        )
        SELECT
            d.document_id,
            d.canonical_symbol,
            d.name,
            d.description,
            ts_rank_cd(d.search_vector, q.query)::double precision AS score
        FROM ai_search.instrument_search_documents AS d
        CROSS JOIN queries AS q
        WHERE d.search_vector @@ q.query
        ORDER BY score DESC, d.document_id
        LIMIT %s
        """,
        (query, limit),
    )
    return cursor.fetchall()


def trigram_search(cursor: Any, query: str, limit: int) -> list[tuple[Any, ...]]:
    """使用 pg_trgm 比较工具主体文本，处理格式差异和少量拼写错误。"""

    cursor.execute(
        """
        SELECT
            d.document_id,
            d.canonical_symbol,
            d.name,
            d.description,
            GREATEST(
                similarity(d.canonical_symbol, %s),
                similarity(d.name, %s),
                similarity(d.description, %s)
            )::double precision AS score
        FROM ai_search.instrument_search_documents AS d
        WHERE GREATEST(
                similarity(d.canonical_symbol, %s),
                similarity(d.name, %s),
                similarity(d.description, %s)
            ) >= %s
        ORDER BY score DESC, d.document_id
        LIMIT %s
        """,
        (query, query, query, query, query, query, TRIGRAM_THRESHOLD, limit),
    )
    return cursor.fetchall()


def embedding_search(cursor: Any, query: str, limit: int) -> tuple[list[tuple[Any, ...]], str | None]:
    """调用当前配置的 Embedding 模型检索。

    数据库安装了 pgvector 时使用 ``halfvec`` HNSW；兼容旧快照或没有 pgvector
    的环境时，同一个 embedding 列使用 JSONB 保存向量，并在应用层计算余弦相似度。
    两种模式对上层返回结构完全一致，未配置 API Key 时仍然优雅降级。
    """

    api_key = os.getenv("EMBEDDING_API_KEY")
    if not api_key:
        return [], "未配置 EMBEDDING_API_KEY，已跳过 Embedding 检索"

    # 复用已有的 API 请求实现，确保批量格式、维度检查和重试策略保持一致。
    from .generate_embeddings import request_embeddings

    settings = embedding_settings()
    model = settings["model"]
    endpoint = settings["endpoint"]
    dimensions = settings["dimensions"]
    try:
        # 必须把用户的原始查询直接交给 Embedding 模型，不做符号转换或补充词。
        vector = request_embeddings([query], api_key, model, endpoint, dimensions)[0]
    except Exception as exc:  # noqa: BLE001 - 语义检索失败时应降级到数据库检索
        return [], f"Embedding 检索失败，已降级：{exc}"

    cursor.execute(
        """
        SELECT udt_name
        FROM information_schema.columns
        WHERE table_schema = 'ai_search'
          AND table_name = 'instrument_search_documents'
          AND column_name = 'embedding'
        """
    )
    storage_type_row = cursor.fetchone()
    storage_type = storage_type_row[0] if storage_type_row else None
    vector_text = "[" + ",".join(str(value) for value in vector) + "]"

    if storage_type == "halfvec":
        cursor.execute(
            """
            SELECT
                d.document_id,
                d.canonical_symbol,
                d.name,
                d.description,
                (1.0 - (d.embedding <=> %s::halfvec))::double precision AS score
            FROM ai_search.instrument_search_documents AS d
            WHERE d.embedding IS NOT NULL
            ORDER BY d.embedding <=> %s::halfvec, d.document_id
            LIMIT %s
            """,
            (vector_text, vector_text, limit),
        )
        return cursor.fetchall(), None

    # 没有 pgvector 时读取 JSONB 向量。当前工具文档数量较小，应用层计算能提供
    # 同样的语义排序；数据量扩大后安装 pgvector 即可切换到上面的 HNSW 路径。
    cursor.execute(
        """
        SELECT document_id, canonical_symbol, name, description, embedding
        FROM ai_search.instrument_search_documents
        WHERE embedding IS NOT NULL
        ORDER BY document_id
        """
    )
    query_norm = math.sqrt(sum(value * value for value in vector))
    scored_rows: list[tuple[Any, ...]] = []
    for document_id, canonical_symbol, name, description, stored_vector in cursor.fetchall():
        if isinstance(stored_vector, str):
            stored_vector = json.loads(stored_vector)
        if not stored_vector or len(stored_vector) != len(vector) or query_norm == 0:
            continue
        stored_norm = math.sqrt(sum(float(value) * float(value) for value in stored_vector))
        if stored_norm == 0:
            continue
        score = sum(float(left) * float(right) for left, right in zip(vector, stored_vector))
        score /= query_norm * stored_norm
        scored_rows.append((document_id, canonical_symbol, name, description, float(score)))
    scored_rows.sort(key=lambda row: (-row[4], row[0]))
    return scored_rows[:limit], "当前数据库未安装 pgvector，Embedding 使用应用层余弦相似度"


def merge_with_rrf(method_results: dict[str, list[tuple[Any, ...]]], limit: int) -> list[dict[str, Any]]:
    """按 canonical_symbol 合并多路结果，并保留每路检索证据。

    ``source.instrument_master.canonical_symbol`` 有唯一约束，因此它才是当前
    金融工具的业务去重键。``document_id`` 只是检索文档的技术标识，不能作为
    不同检索结果之间的业务唯一键。
    """

    merged: dict[str, dict[str, Any]] = {}
    for method, rows in method_results.items():
        for rank, row in enumerate(rows, start=1):
            document_id, canonical_symbol, name, description, score = row
            business_key = str(canonical_symbol)
            candidate = merged.setdefault(
                business_key,
                {
                    "document_id": int(document_id),
                    "canonical_symbol": canonical_symbol,
                    "name": name,
                    "description": description,
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
        key=lambda candidate: (-candidate["rrf_score"], candidate["canonical_symbol"]),
    )[:limit]


def resolve_instrument_candidates(
    cursor: Any,
    candidates: list[dict[str, Any]],
    allowed_instrument_types: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """回查 instrument_master，补充正式 instrument_id 并校验在线状态。

    AI 检索表只保存金融工具的检索文本和向量，不保存正式的 instrument_id。
    因此 RRF 返回候选后，程序使用 canonical_symbol 批量回查 source 表。
    这里保留全部候选，不因为候选数量超过一条而提前丢弃；只有 status 为
    active 的候选才允许进入后续供应商标识查询。
    """

    if not candidates:
        return [], {"resolved": 0, "inactive": 0, "not_found": 0}

    symbols = [candidate["canonical_symbol"] for candidate in candidates]
    cursor.execute(
        """
        SELECT instrument_id,
               canonical_symbol,
               instrument_type,
               name,
               description,
               status
        FROM source.instrument_master
        WHERE canonical_symbol = ANY(%s)
        """,
        (symbols,),
    )
    master_rows = {
        row[1]: {
            "instrument_id": row[0],
            "canonical_symbol": row[1],
            "instrument_type": row[2],
            "master_name": row[3],
            "master_description": row[4],
            "status": row[5],
        }
        for row in cursor.fetchall()
    }

    normalized_types = {
        str(value).strip().upper() for value in (allowed_instrument_types or set())
    }
    resolution_counts = {"resolved": 0, "inactive": 0, "not_found": 0}
    if normalized_types:
        # 只有路线明确声明工具类型白名单时才增加该统计项，保持旧调用方的
        # 返回结构兼容；宏观路线会用它排除外汇等非宏观工具。
        resolution_counts["unsupported_type"] = 0
    resolved_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        resolved = dict(candidate)
        master = master_rows.get(candidate["canonical_symbol"])
        if master is None:
            resolved.update(
                {
                    "instrument_id": None,
                    "instrument_type": None,
                    "master_name": None,
                    "master_description": None,
                    "status": None,
                    "resolution_status": "not_found",
                    "eligible_for_next_step": False,
                }
            )
            resolution_counts["not_found"] += 1
        elif normalized_types and str(master["instrument_type"]).upper() not in normalized_types:
            resolved.update(
                {
                    **master,
                    "resolution_status": "unsupported_type",
                    "eligible_for_next_step": False,
                }
            )
            resolution_counts["unsupported_type"] += 1
        else:
            is_active = str(master["status"]).lower() == "active"
            resolution_status = "resolved" if is_active else "inactive"
            resolved.update(
                {
                    **master,
                    "resolution_status": resolution_status,
                    "eligible_for_next_step": is_active,
                }
            )
            resolution_counts[resolution_status] += 1
        resolved_candidates.append(resolved)

    return resolved_candidates, resolution_counts


def resolve_instrument_identifiers(
    cursor: Any,
    instrument_id: str,
    provider: str | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """查询并校验最终工具的有效供应商标识。

    ``effective_date`` 使用包含边界，``expire_date`` 使用不包含边界：
    标识在 effective_date 当天生效，在 expire_date 当天失效。供应商未指定时，
    返回所有当前有效标识；只有一条时自动确定，多条时保留分支而不静默选择。
    """

    query_date = as_of_date or date.today()
    sql = """
        SELECT instrument_id,
               provider,
               identifier_type,
               identifier,
               effective_date,
               expire_date
        FROM source.instrument_identifier
        WHERE instrument_id = %s
          AND effective_date <= %s
          AND (expire_date IS NULL OR %s < expire_date)
    """
    parameters: list[Any] = [instrument_id, query_date, query_date]
    if provider:
        sql += " AND provider = %s"
        parameters.append(provider)
    sql += " ORDER BY provider, effective_date DESC, identifier"
    cursor.execute(sql, tuple(parameters))

    identifiers = [
        {
            "instrument_id": row[0],
            "provider": row[1],
            "identifier_type": row[2],
            "identifier": row[3],
            "effective_date": row[4].isoformat() if row[4] else None,
            "expire_date": row[5].isoformat() if row[5] else None,
        }
        for row in cursor.fetchall()
    ]
    if not identifiers:
        status = "not_found"
        selected = None
    elif len(identifiers) == 1:
        status = "resolved"
        selected = identifiers[0]
    else:
        status = "multiple"
        selected = None

    return {
        "instrument_id": instrument_id,
        "provider_requested": provider,
        "as_of_date": query_date.isoformat(),
        "status": status,
        "selected": selected,
        "candidates": identifiers,
    }


def search_instrument_documents(
    cursor: Any,
    query: str,
    limit: int = DEFAULT_LIMIT,
    use_embedding: bool = True,
    use_candidate_llm: bool = True,
    provider: str | None = None,
    identifier_as_of_date: date | None = None,
    allowed_instrument_types: set[str] | None = None,
    resolve_identifier: bool = True,
    trace_callback: TraceCallback | None = None,
) -> dict[str, Any]:
    """执行金融工具多路检索，并返回候选文档及检索状态。

    ``trace_callback`` 是给本地测试工作台使用的可选回调。它不会改变查询结果，
    只会在每个模块开始和结束时报告输入、输出、状态及耗时；命令行调用不传入
    该回调，因此仍然保持原来的 JSON 输出方式。
    """

    if not query.strip():
        raise ValueError("查询文本不能为空")
    if limit < 1 or limit > 100:
        raise ValueError("limit 必须在 1 到 100 之间")

    method_results: dict[str, list[tuple[Any, ...]]] = {
        "exact": _run_traced(
            trace_callback,
            "exact_match",
            {"query": query, "limit": limit, "match_fields": ["canonical_symbol"]},
            lambda: exact_search(cursor, query, limit),
            _rows_for_trace,
        ),
        "keyword": _run_traced(
            trace_callback,
            "keyword_search",
            {"query": query, "limit": limit, "search_field": "search_vector"},
            lambda: keyword_search(cursor, query, limit),
            _rows_for_trace,
        ),
        "pg_trgm": _run_traced(
            trace_callback,
            "pg_trgm_search",
            {
                "query": query,
                "limit": limit,
                "match_fields": ["canonical_symbol", "name", "description"],
                "threshold": TRIGRAM_THRESHOLD,
            },
            lambda: trigram_search(cursor, query, limit),
            _rows_for_trace,
        ),
    }
    warnings: list[str] = []

    if use_embedding:
        embedding_result = _run_traced(
            trace_callback,
            "embedding_search",
            {
                "query": query,
                "limit": limit,
                "model": os.getenv(
                    "EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"
                ),
                "dimensions": int(os.getenv("EMBEDDING_DIMENSIONS", "2048")),
            },
            lambda: embedding_search(cursor, query, limit),
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
            "embedding_search",
            {"query": query, "limit": limit, "enabled": False},
            lambda: ([], "Embedding 检索已由请求关闭"),
            _embedding_result_for_trace,
        )

    candidates = _run_traced(
        trace_callback,
        "rrf_merge",
        {"limit": limit, "methods": {name: len(rows) for name, rows in method_results.items()}},
        lambda: merge_with_rrf(method_results, limit),
        lambda value: value,
    )
    resolved_candidates, resolution_counts = _run_traced(
        trace_callback,
        "instrument_master",
        {
            "candidate_symbols": [candidate["canonical_symbol"] for candidate in candidates],
            "table": "source.instrument_master",
            "validate": "status = active",
            "allowed_instrument_types": sorted(allowed_instrument_types or set()),
        },
        lambda: resolve_instrument_candidates(
            cursor,
            candidates,
            allowed_instrument_types=allowed_instrument_types,
        ),
        lambda value: {"candidates": value[0], "counts": value[1]},
    )
    model_selection: dict[str, Any] | None = None
    identifier_resolution: dict[str, Any] | None = None
    if use_candidate_llm:
        try:
            model_selection = _run_traced(
                trace_callback,
                "candidate_selector",
                {
                    "query": query,
                    "candidate_count": len(resolved_candidates),
                    "allowed_instrument_ids": [
                        candidate.get("instrument_id")
                        for candidate in resolved_candidates
                        if candidate.get("eligible_for_next_step")
                    ],
                    "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                },
                lambda: select_instrument_candidate(query, resolved_candidates),
                lambda value: value,
            )
        except Exception as exc:  # noqa: BLE001 - 模型失败时禁止越权继续查询
            warnings.append(f"候选筛选模型不可用，已停止最终工具选择：{exc}")
            model_selection = {
                "decision": "unavailable",
                "instrument_id": None,
                "canonical_symbol": None,
                "confidence": 0,
                "reason": "候选筛选模型调用或校验失败",
                "candidate": None,
            }
    else:
        _run_traced(
            trace_callback,
            "candidate_selector",
            {"query": query, "enabled": False},
            lambda: {
                "decision": "skipped",
                "reason": "候选筛选模型已由请求关闭",
            },
            lambda value: value,
        )
    if model_selection and model_selection.get("decision") == "select" and resolve_identifier:
        identifier_query_date = identifier_as_of_date or date.today()
        identifier_resolution = _run_traced(
            trace_callback,
            "instrument_identifier",
            {
                "instrument_id": model_selection["instrument_id"],
                "provider": provider,
                "as_of_date": identifier_query_date.isoformat(),
                "validate": "effective_date <= as_of_date < expire_date",
            },
            lambda: resolve_instrument_identifiers(
                cursor,
                model_selection["instrument_id"],
                provider=provider,
                as_of_date=identifier_query_date,
            ),
            lambda value: value,
        )
    else:
        _run_traced(
            trace_callback,
            "instrument_identifier",
            {
                "instrument_id": model_selection.get("instrument_id") if model_selection else None,
                "reason": (
                    "当前是 instrument_master 标准化查询，按路由要求跳过供应商标识查询"
                    if model_selection and model_selection.get("decision") == "select"
                    and not resolve_identifier
                    else "没有最终选中的 active instrument_id，跳过供应商标识查询"
                ),
            },
            lambda: {"status": "skipped", "candidates": []},
            lambda value: value,
        )

    return {
        "query": query,
        "methods": {name: len(rows) for name, rows in method_results.items()},
        "warnings": warnings,
        "master_resolution": resolution_counts,
        "candidates": resolved_candidates,
        "model_selection": model_selection,
        "identifier_resolution": identifier_resolution,
    }


def main() -> int:
    """连接数据库、执行检索并以 JSON 输出结果。"""

    args = parse_args()
    query = " ".join(args.query).strip()
    with psycopg2.connect(**connection_kwargs(args)) as connection:
        with connection.cursor() as cursor:
            result = search_instrument_documents(
                cursor,
                query,
                limit=args.limit,
                use_embedding=not args.no_embedding,
                use_candidate_llm=not args.no_candidate_llm,
                provider=args.provider,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
