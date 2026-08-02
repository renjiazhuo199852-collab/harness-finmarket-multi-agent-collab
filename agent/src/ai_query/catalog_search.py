"""AI 数据目录的精确、关键词、向量和 RRF 混合检索。

检索结果只描述“可能应该查询什么”，不直接返回行情业务行。这样 Agent
可以先看到数据集、字段、金融工具和关系候选，再提交结构化查询计划；真正的
数据库查询由 :mod:`src.ai_query.query_executor` 负责校验和执行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx

from src.config.accessor import get_env_config
from src.config.env_schema import AIQueryConfig
from src.market_database import MarketDatabaseClient, MarketDatabaseUnavailable

_ALIAS_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9./=_-]{1,50}")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_SEARCH_LIMIT = 50
_RRF_K = 60
_RRF_CANDIDATE_LIMIT = 20
_EMBEDDING_DIMENSION = 2048
_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    # 只把用户意图扩展为目录中常见的业务词，不绑定任何具体数据集或表名。
    "价格": ("price", "last", "bid", "ask", "mid"),
    "报价": ("price", "quote", "last", "bid", "ask", "mid"),
    "最新": ("latest", "current", "snapshot"),
    "当前": ("latest", "current", "snapshot"),
    "买价": ("bid",),
    "卖价": ("ask",),
}


class CatalogSearchError(ValueError):
    """表示目录检索输入或数据库返回不符合预期。"""


class EmbeddingUnavailable(RuntimeError):
    """表示向量服务暂时不可用，调用方可以降级到关键词检索。"""


class EmbeddingClient(Protocol):
    """Embedding 客户端的最小接口，便于单元测试注入假的向量服务。"""

    def embed(self, text: str) -> list[float]:
        """把一段查询文本转换为向量。"""


class ZhipuEmbeddingClient:
    """调用智谱 Embedding-3 的同步客户端。

    查询向量只在检索时临时生成，不写回数据库。数据文档的向量已经由独立的
    ``sql/embed_ai_documents.py`` 生成；API Key 和异常响应正文都不会进入日志。
    """

    def __init__(self, config: AIQueryConfig) -> None:
        self._config = config

    def embed(self, text: str) -> list[float]:
        """调用 Embedding API，并严格检查模型和向量维度。"""
        if not self._config.embedding_configured:
            raise EmbeddingUnavailable("Embedding 未配置 API Key，已降级到关键词检索。")

        try:
            response = httpx.post(
                self._config.zhipu_embedding_endpoint,
                headers={
                    "Authorization": f"Bearer {self._config.zhipu_api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self._config.zhipu_embedding_model, "input": [text]},
                timeout=30.0,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # 不把供应商返回正文放进错误，防止响应中意外携带敏感内容。
            raise EmbeddingUnavailable(
                f"Embedding 服务调用失败：{type(exc).__name__}"
            ) from exc

        data = body.get("data") if isinstance(body, dict) else None
        vector = data[0].get("embedding") if isinstance(data, list) and data else None
        if not isinstance(vector, list) or len(vector) != _EMBEDDING_DIMENSION:
            actual_dimension = len(vector) if isinstance(vector, list) else None
            raise EmbeddingUnavailable(
                f"Embedding 返回维度异常：expected={_EMBEDDING_DIMENSION}, actual={actual_dimension}"
            )
        try:
            return [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingUnavailable(
                "Embedding 返回了无法转换为数字的向量。"
            ) from exc


@dataclass(frozen=True)
class CatalogCandidate:
    """一个可供 Agent 选择的数据目录候选。"""

    doc_id: str
    doc_type: str
    title: str
    dataset_id: str | None
    source_table: str | None
    source_key: str | None
    source_version: str | None
    score: float
    exact_match: bool = False
    keyword_rank: int | None = None
    vector_rank: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """转换为稳定的 Tool/脚本 JSON 结构。"""
        return {
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "title": self.title,
            "dataset_id": self.dataset_id,
            "source_table": self.source_table,
            "source_key": self.source_key,
            "source_version": self.source_version,
            "score": self.score,
            "exact_match": self.exact_match,
            "keyword_rank": self.keyword_rank,
            "vector_rank": self.vector_rank,
        }


class AICatalogSearch:
    """在本机 AI 数据库中执行可解释的混合目录检索。"""

    def __init__(
        self,
        client: MarketDatabaseClient | Any | None = None,
        *,
        config: AIQueryConfig | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self._config = config or get_env_config().ai_query
        self._client = client or self._build_client()
        self._embedding_client = embedding_client or ZhipuEmbeddingClient(self._config)

    @property
    def is_configured(self) -> bool:
        """返回 AI 数据库是否已显式配置。"""
        return bool(self._client.is_configured)

    def search(self, question: Any, *, limit: Any = 10) -> dict[str, Any]:
        """根据自然语言问题返回目录候选，不查询业务数据行。"""
        clean_question = _required_question(question)
        clean_limit = _bounded_limit(limit)
        if not self.is_configured:
            raise MarketDatabaseUnavailable(
                "AI query database is not configured; set AI_QUERY_ENABLED and AI_QUERY_DB_* values"
            )

        exact_candidates = self._exact_instrument_candidates(clean_question)
        keyword_candidates = self._keyword_candidates(clean_question)
        vector_candidates: list[CatalogCandidate] = []
        warnings: list[str] = []
        try:
            vector_candidates = self._vector_candidates(clean_question)
        except EmbeddingUnavailable as exc:
            warnings.append(str(exc))

        merged = _merge_rrf(exact_candidates, keyword_candidates, vector_candidates)
        # RRF 负责融合两路检索分数；上下文补全负责保证 Agent 能同时看到
        # “工具、数据集、字段、关系”四类目录信息。向量近邻可能召回许多
        # EUR/XXX 工具，但这些工具不能替代已命中数据集的字段和关联关系。
        merged = self._expand_catalog_context(merged)
        selected = _select_catalog_context(merged, limit=clean_limit)
        result_candidates = [candidate.as_dict() for candidate in selected]
        source_versions = sorted(
            {
                value
                for candidate in result_candidates
                if (value := candidate.get("source_version"))
            }
        )
        return {
            "ok": True,
            "question": clean_question,
            "retrieval_mode": "hybrid" if vector_candidates else "keyword_fallback",
            "candidates": result_candidates,
            "count": len(result_candidates),
            "source_versions": source_versions,
            "warnings": warnings,
        }

    def _build_client(self) -> MarketDatabaseClient:
        """把 AIQueryConfig 转成已有的只读 PostgreSQL 客户端配置。"""
        from src.config.env_schema import MarketDatabaseConfig

        return MarketDatabaseClient(
            MarketDatabaseConfig(
                enabled=self._config.enabled,
                host=self._config.host,
                port=self._config.port,
                database=self._config.database,
                user=self._config.user,
                password=self._config.password,
                connect_timeout_seconds=self._config.connect_timeout_seconds,
                statement_timeout_ms=self._config.statement_timeout_ms,
            )
        )

    def _exact_instrument_candidates(self, question: str) -> list[CatalogCandidate]:
        """优先从主数据和供应商标识表解析明确的工具代码。"""
        aliases = _extract_aliases(question)
        if not aliases:
            return []
        normalized_aliases = sorted(
            {_normalize_symbol(alias) for alias in aliases if _normalize_symbol(alias)}
        )
        rows = self._client.fetch_all(
            """
            SELECT im.instrument_id, im.canonical_symbol, im.name,
                   ii.identifier
            FROM source.instrument_master AS im
            LEFT JOIN source.instrument_identifier AS ii
              ON ii.instrument_id = im.instrument_id
            WHERE upper(im.instrument_id) = ANY(%s)
               OR upper(im.canonical_symbol) = ANY(%s)
               OR regexp_replace(upper(im.canonical_symbol), '[^A-Z0-9]', '', 'g') = ANY(%s)
               OR upper(ii.identifier) = ANY(%s)
            ORDER BY im.instrument_id, ii.identifier
            """,
            (
                [alias.upper() for alias in aliases],
                [alias.upper() for alias in aliases],
                normalized_aliases or ["__NO_ALIAS__"],
                [alias.upper() for alias in aliases],
            ),
        )
        instrument_ids = list(dict.fromkeys(str(row["instrument_id"]) for row in rows))
        if not instrument_ids:
            return []
        documents = self._documents_by_ids(
            [f"instrument:{instrument_id}" for instrument_id in instrument_ids]
        )
        return [
            CatalogCandidate(
                doc_id=document["doc_id"],
                doc_type=document["doc_type"],
                title=document["title"],
                dataset_id=document.get("dataset_id"),
                source_table=document.get("source_table"),
                source_key=document.get("source_key"),
                source_version=document.get("source_version"),
                score=1.0,
                exact_match=True,
            )
            for document in documents
        ]

    def _keyword_candidates(self, question: str) -> list[CatalogCandidate]:
        """使用 PostgreSQL 全文索引返回关键词候选。"""
        search_text = _keyword_query_text(question)
        rows = self._client.fetch_all(
            """
            WITH query AS (
                SELECT websearch_to_tsquery('simple', %s) AS terms
            )
            SELECT d.doc_id, d.doc_type, d.title, d.dataset_id,
                   d.source_table, d.source_key, d.source_version,
                   ts_rank_cd(d.search_vector, query.terms) AS score
            FROM ai.search_documents AS d
            CROSS JOIN query
            WHERE d.search_vector @@ query.terms
            ORDER BY score DESC, d.doc_id
            LIMIT %s
            """,
            (search_text, _RRF_CANDIDATE_LIMIT),
        )
        return [
            _candidate_from_row(row, rank=index)
            for index, row in enumerate(rows, start=1)
        ]

    def _vector_candidates(self, question: str) -> list[CatalogCandidate]:
        """调用 Embedding API 后执行半精度向量余弦近邻查询。"""
        vector = self._embedding_client.embed(question)
        vector_literal = "[" + ",".join(str(value) for value in vector) + "]"
        rows = self._client.fetch_all(
            """
            SELECT d.doc_id, d.doc_type, d.title, d.dataset_id,
                   d.source_table, d.source_key, d.source_version,
                   1 - (d.embedding <=> %s::halfvec) AS score
            FROM ai.search_documents AS d
            WHERE d.embedding IS NOT NULL
            ORDER BY d.embedding <=> %s::halfvec, d.doc_id
            LIMIT %s
            """,
            (vector_literal, vector_literal, _RRF_CANDIDATE_LIMIT),
        )
        return [
            _candidate_from_row(row, rank=index)
            for index, row in enumerate(rows, start=1)
        ]

    def _documents_by_ids(self, doc_ids: list[str]) -> list[dict[str, Any]]:
        """读取精确命中文档，避免把完整业务正文带入检索结果。"""
        if not doc_ids:
            return []
        return self._client.fetch_all(
            """
            SELECT doc_id, doc_type, title, dataset_id, source_table, source_key, source_version
            FROM ai.search_documents
            WHERE doc_id = ANY(%s)
            ORDER BY doc_id
            """,
            (doc_ids,),
        )

    def _expand_catalog_context(
        self, candidates: list[CatalogCandidate]
    ) -> list[CatalogCandidate]:
        """根据已命中的数据集补齐字段和允许使用的语义关系。

        这一步仍然只读取 ``ai`` Schema 的目录文档，不查询 ``source`` 业务
        数据。数据集 ID 和物理表名来自检索候选，而关系表只负责把同一物理
        表对应的安全关联文档补回来，因此不会把用户输入拼接进 SQL。
        """
        ranked = sorted(
            candidates,
            key=lambda item: (-int(item.exact_match), -item.score, item.doc_id),
        )
        # 只以最高分数据集作为当前查询上下文。向量结果中可能同时出现
        # latest_prices、market_bars 等相邻数据集，全部展开会把旁支关系
        # 混入当前查询计划。
        context_anchor = next(
            (
                candidate
                for candidate in ranked
                if candidate.doc_type == "dataset"
                and candidate.dataset_id
                and candidate.source_table
            ),
            None,
        )
        if context_anchor is None:
            context_anchor = next(
                (
                    candidate
                    for candidate in ranked
                    if candidate.doc_type == "field"
                    and candidate.dataset_id
                    and candidate.source_table
                ),
                None,
            )
        dataset_ids = [context_anchor.dataset_id] if context_anchor else []
        source_tables = [context_anchor.source_table] if context_anchor else []
        if not dataset_ids or not source_tables:
            return candidates

        rows = self._client.fetch_all(
            """
            SELECT d.doc_id, d.doc_type, d.title, d.dataset_id,
                   d.source_table, d.source_key, d.source_version,
                   0::double precision AS score,
                   r.left_table, r.right_table
            FROM ai.search_documents AS d
            LEFT JOIN ai.semantic_relations AS r
              ON r.relation_id = d.relation_id
            WHERE (
                d.doc_type = 'field'
                AND d.dataset_id = ANY(%s)
            )
               OR (
                d.doc_type = 'relation'
                AND r.is_enabled
            )
            ORDER BY d.doc_type, d.doc_id
            """,
            (dataset_ids,),
        )
        relation_rows = [row for row in rows if row.get("doc_type") == "relation"]
        relevant_relation_ids = _relation_path_ids(source_tables, relation_rows)
        by_id = {candidate.doc_id: candidate for candidate in candidates}
        if relation_rows and relevant_relation_ids:
            # RRF 可能已经带入了向量近邻的旁支关系；只保留从当前物理表
            # 到 instrument_master 的最短关系路径，避免 Agent 误选兄弟表。
            by_id = {
                doc_id: candidate
                for doc_id, candidate in by_id.items()
                if candidate.doc_type != "relation" or doc_id in relevant_relation_ids
            }
        for row in rows:
            doc_id = str(row["doc_id"])
            if row.get("doc_type") != "relation" or doc_id in relevant_relation_ids:
                if doc_id not in by_id:
                    by_id[doc_id] = _candidate_from_context_row(row)
        return list(by_id.values())


def _candidate_from_row(row: dict[str, Any], *, rank: int) -> CatalogCandidate:
    """把数据库一行转换为统一候选对象。"""
    score = row.get("score", 0.0)
    if isinstance(score, Decimal):
        score = float(score)
    return CatalogCandidate(
        doc_id=str(row["doc_id"]),
        doc_type=str(row["doc_type"]),
        title=str(row["title"]),
        dataset_id=row.get("dataset_id"),
        source_table=row.get("source_table"),
        source_key=row.get("source_key"),
        source_version=row.get("source_version"),
        score=float(score),
        keyword_rank=rank,
    )


def _candidate_from_context_row(row: dict[str, Any]) -> CatalogCandidate:
    """把上下文补全查询返回的一行转换成零分候选。

    已经参与关键词或向量排名的文档保留原始 RRF 分数；仅由上下文补全
    找到的文档不伪造检索排名，后续只在同一目录上下文中提供给 Agent。
    """
    return CatalogCandidate(
        doc_id=str(row["doc_id"]),
        doc_type=str(row["doc_type"]),
        title=str(row["title"]),
        dataset_id=row.get("dataset_id"),
        source_table=row.get("source_table"),
        source_key=row.get("source_key"),
        source_version=row.get("source_version"),
        score=0.0,
    )


def _relation_path_ids(
    source_tables: list[str], rows: list[dict[str, Any]]
) -> set[str]:
    """找出物理数据表到工具主表的最短关系路径。

    ``semantic_relations`` 是小型元数据图：表名是节点，关系文档是边。
    通过最短路径选择可以保留 ``latest_prices -> instrument_identifier ->
    instrument_master`` 的两条边，同时排除同样连接到
    ``instrument_identifier``、但通向 ``market_bars`` 的旁支关系。
    """
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        relation_id = str(row.get("doc_id", ""))
        left_table = row.get("left_table")
        right_table = row.get("right_table")
        if not relation_id or not left_table or not right_table:
            continue
        left = str(left_table)
        right = str(right_table)
        adjacency.setdefault(left, []).append((right, relation_id))
        adjacency.setdefault(right, []).append((left, relation_id))

    target = "instrument_master"
    result: set[str] = set()
    for source_table in source_tables:
        queue: list[tuple[str, tuple[str, ...], frozenset[str]]] = [
            (source_table, (), frozenset({source_table}))
        ]
        while queue:
            node, path, visited = queue.pop(0)
            if node == target:
                result.update(path)
                break
            for neighbor, relation_id in adjacency.get(node, []):
                if neighbor in visited:
                    continue
                queue.append((neighbor, (*path, relation_id), visited | {neighbor}))

    if result:
        return result
    # 没有工具主表路径时，至少保留当前数据表的直接关系，供未来非工具类
    # 数据集扩展使用；这不会凭空制造一条关联路径。
    return {
        relation_id
        for row in rows
        for relation_id in [str(row.get("doc_id", ""))]
        if row.get("left_table") in source_tables
        or row.get("right_table") in source_tables
    }


def _merge_rrf(
    exact_candidates: list[CatalogCandidate],
    keyword_candidates: list[CatalogCandidate],
    vector_candidates: list[CatalogCandidate],
) -> list[CatalogCandidate]:
    """合并两路候选，并把精确工具代码放在语义近邻之前。"""
    merged: dict[str, dict[str, Any]] = {}

    for candidate in exact_candidates:
        item = merged.setdefault(
            candidate.doc_id, {"candidate": candidate, "score": 0.0}
        )
        item["exact_match"] = True
        item["score"] += 1.0

    for source_name, candidates in (
        ("keyword_rank", keyword_candidates),
        ("vector_rank", vector_candidates),
    ):
        for rank, candidate in enumerate(candidates, start=1):
            item = merged.setdefault(
                candidate.doc_id, {"candidate": candidate, "score": 0.0}
            )
            item["score"] += 1.0 / (_RRF_K + rank)
            item[source_name] = rank

    result: list[CatalogCandidate] = []
    for item in merged.values():
        candidate = item["candidate"]
        result.append(
            CatalogCandidate(
                doc_id=candidate.doc_id,
                doc_type=candidate.doc_type,
                title=candidate.title,
                dataset_id=candidate.dataset_id,
                source_table=candidate.source_table,
                source_key=candidate.source_key,
                source_version=candidate.source_version,
                score=round(float(item["score"]), 8),
                exact_match=bool(item.get("exact_match", False)),
                keyword_rank=item.get("keyword_rank"),
                vector_rank=item.get("vector_rank"),
            )
        )
    return sorted(
        result, key=lambda item: (-int(item.exact_match), -item.score, item.doc_id)
    )


def _select_catalog_context(
    candidates: list[CatalogCandidate], *, limit: int
) -> list[CatalogCandidate]:
    """在 RRF 候选中保留一个可供 Agent 规划查询的最小目录上下文。

    选择顺序是：精确工具、最高分数据集、相关语义关系、该数据集的高分
    字段，最后才用剩余 RRF 候选填满数量。这样默认 ``limit=10`` 时，首条
    EUR/USD 报价路径通常能同时看到一个工具、一个数据集、两条关联关系和
    五个报价字段；如果调用方把 limit 调得更小，则仍严格遵守调用方限制。
    """
    ranked = sorted(
        candidates, key=lambda item: (-int(item.exact_match), -item.score, item.doc_id)
    )
    selected: list[CatalogCandidate] = []
    selected_ids: set[str] = set()

    def add(items: list[CatalogCandidate]) -> None:
        for item in items:
            if item.doc_id in selected_ids or len(selected) >= limit:
                continue
            selected.append(item)
            selected_ids.add(item.doc_id)

    add([item for item in ranked if item.exact_match])
    dataset_items = [item for item in ranked if item.doc_type == "dataset"]
    add(dataset_items[:1])

    dataset_ids = {item.dataset_id for item in dataset_items[:1] if item.dataset_id}
    relation_items = [item for item in ranked if item.doc_type == "relation"]
    add(relation_items)

    field_items = [
        item
        for item in ranked
        if item.doc_type == "field"
        and (not dataset_ids or item.dataset_id in dataset_ids)
    ]
    # 有 RRF 分数的字段先入选，上下文补齐的零分字段只用于补足剩余位置。
    add([item for item in field_items if item.score > 0])
    add([item for item in field_items if item.score <= 0])
    add(ranked)
    return selected


def _required_question(value: Any) -> str:
    """校验自然语言问题，避免空问题触发无意义的向量请求。"""
    if not isinstance(value, str) or not value.strip():
        raise CatalogSearchError("question 必须是非空字符串。")
    question = value.strip()
    if len(question) > 2000:
        raise CatalogSearchError("question 长度不能超过 2000 个字符。")
    return question


def _bounded_limit(value: Any) -> int:
    """限制返回候选数，避免 Agent 通过参数读取过量元数据。"""
    if value is None:
        return 10
    if isinstance(value, bool) or not isinstance(value, int):
        raise CatalogSearchError("limit 必须是整数。")
    if value < 1 or value > _MAX_SEARCH_LIMIT:
        raise CatalogSearchError(f"limit 必须在 1 到 {_MAX_SEARCH_LIMIT} 之间。")
    return value


def _extract_aliases(question: str) -> list[str]:
    """提取可能的工具/供应商代码；自然语言部分仍交给全文和向量检索。"""
    aliases: list[str] = []
    for match in _ALIAS_PATTERN.findall(question):
        value = match.strip(".,，。:：;；()（）")
        if value and value.upper() not in {item.upper() for item in aliases}:
            aliases.append(value)
    return aliases


def _keyword_query_text(question: str) -> str:
    """将中文业务意图扩展为可检索的目录词，并用 OR 保留多类候选。

    目录文档同时包含英文数据集名、业务字段名和中文说明。若把“查询 EUR/USD
    最新价格”原样作为全文查询，PostgreSQL 会把所有词按 AND 处理，容易因为
    中文说明与英文目录不完全相同而没有数据集/字段命中。这里仍然只生成检索
    词，不决定要查询哪张表；最终选择交给 RRF 和后续结构化计划校验。
    """
    terms: list[str] = []
    for alias in _extract_aliases(question):
        if alias.upper() not in {term.upper() for term in terms}:
            terms.append(alias)
    for marker, synonyms in _QUERY_SYNONYMS.items():
        if marker in question:
            terms.extend(synonyms)
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,30}", question):
        terms.append(word)
    unique_terms = list(dict.fromkeys(term for term in terms if term))
    return " OR ".join(unique_terms) or question


def _normalize_symbol(value: str) -> str:
    """移除工具代码中的分隔符，统一 EURUSD 与 EUR/USD 的匹配形式。"""
    return "".join(character for character in value.upper() if character.isalnum())


def quote_identifier(value: str) -> str:
    """安全引用来自受控数据库元数据的标识符。

    用户输入永远不会直接进入这里；即使元数据被误配置，也要求它符合普通
    PostgreSQL 标识符语法，避免把表名或列名拼接成可执行 SQL 片段。
    """
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise CatalogSearchError(f"数据库标识符不符合安全格式：{value!r}")
    return f'"{value}"'
