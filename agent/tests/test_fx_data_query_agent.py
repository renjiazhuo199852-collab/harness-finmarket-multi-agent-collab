"""Tests for the service-backed FX data query boundary."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastmcp.exceptions import McpError
from mcp import types as mcp_types

from src.fx_debate.context import build_evidence_context
from src.fx_debate.data_query_agent import (
    AiSearchClient,
    FxDataQueryAgent,
    FxDataServiceError,
    McpAiSearchClient,
    _build_mcp_child_env,
    _load_mcp_dotenv,
)
from src.fx_debate.evidence_factory import FxEvidenceFactory
from src.fx_debate.evidence_sources import AiSearchFxEvidenceSource
from src.fx_debate.models import ResolvedFxDebateRequest, RunOptions


def _context():
    request = ResolvedFxDebateRequest(
        status="resolved",
        asset_class="fx",
        instrument_type="spot",
        pair_class="major",
        canonical_symbol="EURUSD",
        display_symbol="EUR/USD",
        base_currency="EUR",
        quote_currency="USD",
        requested_base_currency="EUR",
        requested_quote_currency="USD",
        inverted=False,
        horizon="2 weeks",
        timeframe="4H/1D",
    )
    return build_evidence_context(
        request,
        RunOptions(
            request_id="data-service-test",
            as_of=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        ),
    )


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_client_sends_only_bounded_natural_language_request() -> None:
    seen: dict[str, object] = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response({"status": "success", "schema_version": "fx-evidence.v1", "data": []})

    result = AiSearchClient("http://127.0.0.1:8011", opener=opener).search(
        "market_bars_search",
        "查询 EURUSD 最近一个月的日线行情",
        start_date="2026-07-18",
        end_date="2026-08-18",
        max_rows=80,
    )

    assert result["status"] == "success"
    assert seen["url"] == "http://127.0.0.1:8011/v1/evidence/market_bars_search"
    assert seen["payload"] == {
        "query": "查询 EURUSD 最近一个月的日线行情",
        "max_rows": 80,
        "start_date": "2026-07-18",
        "end_date": "2026-08-18",
    }


def test_client_surfaces_structured_provider_rejection() -> None:
    def opener(_request, **_kwargs):
        return _Response(
            {
                "status": "rejected",
                "schema_version": "fx-evidence.v1",
                "code": "DATASET_NOT_FOUND",
                "message": "未找到匹配的数据集",
                "data": [],
            }
        )

    with pytest.raises(FxDataServiceError, match="未找到匹配") as error:
        AiSearchClient("http://data.test", opener=opener).search(
            "unified_search", "查询 EURUSD 的数据"
        )
    assert error.value.code == "DATASET_NOT_FOUND"


def test_query_agent_builds_four_domain_debate_plan() -> None:
    class _NoopClient:
        pass

    plan = FxDataQueryAgent(_NoopClient()).plan_for_debate(_context())
    assert [item.domain for item in plan] == ["prices", "bars", "macro", "news"]
    assert {item.tool for item in plan} == {"unified_search"}
    assert "EUR/USD" in plan[1].query
    assert plan[1].start_date == _context().market_start_time.date().isoformat()
    assert plan[1].end_date == "2026-08-18"


def test_direct_query_defaults_to_unified_search() -> None:
    seen: list[str] = []

    class _Client:
        def search(self, tool, query, **_kwargs):
            seen.append(tool)
            return {"status": "success", "data": [{"query": query}]}

    result = FxDataQueryAgent(_Client()).query("查询 EURUSD 的最新行情")

    assert seen == ["unified_search"]
    assert result["status"] == "success"


def test_mcp_child_disables_fastmcp_update_check_without_losing_service_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP startup must not depend on PyPI/proxy configuration."""

    monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", "stable")
    child_env = _build_mcp_child_env({"AI_SEARCH_DB_HOST": "127.0.0.1"})

    assert child_env["FASTMCP_CHECK_FOR_UPDATES"] == "off"
    assert child_env["AI_SEARCH_DB_HOST"] == "127.0.0.1"


def test_mcp_child_maps_existing_market_database_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP service reuses the operator's existing local DB configuration."""

    monkeypatch.setenv("MARKET_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("MARKET_DB_PORT", "15433")
    monkeypatch.setenv("MARKET_DB_NAME", "icbc_shared")
    monkeypatch.setenv("MARKET_DB_USER", "icbc_collab")
    monkeypatch.setenv("MARKET_DB_PASSWORD", "local-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "Pro/zai-org/GLM-5.1")
    for key in (
        "AI_SEARCH_DB_HOST",
        "AI_SEARCH_DB_PORT",
        "AI_SEARCH_DB_NAME",
        "AI_SEARCH_DB_USER",
        "AI_SEARCH_DB_PASSWORD",
        "LLM_API_KEY",
        "EMBEDDING_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    child_env = _build_mcp_child_env({})

    assert child_env["AI_SEARCH_DB_HOST"] == "127.0.0.1"
    assert child_env["AI_SEARCH_DB_PORT"] == "15433"
    assert child_env["AI_SEARCH_DB_NAME"] == "icbc_shared"
    assert child_env["AI_SEARCH_DB_USER"] == "icbc_collab"
    assert child_env["AI_SEARCH_DB_PASSWORD"] == "local-secret"
    assert child_env["LLM_API_KEY"] == "provider-secret"
    assert child_env["EMBEDDING_API_KEY"] == "provider-secret"
    assert child_env["LLM_BASE_URL"] == "https://api.siliconflow.cn/v1"
    assert child_env["LLM_MODEL"] == "Pro/zai-org/GLM-5.1"


def test_mcp_repository_env_overrides_inherited_provider_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EMBEDDING_MODEL=doubao-embedding-vision\n"
        "EMBEDDING_API_KEY=ark-test-key\n",
        encoding="utf-8",
    )

    assert _load_mcp_dotenv(env_file) == {
        "EMBEDDING_MODEL": "doubao-embedding-vision",
        "EMBEDDING_API_KEY": "ark-test-key",
    }


def test_mcp_connection_closed_is_exposed_as_structured_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashed MCP child must not escape as an unhandled raw exception."""

    client = McpAiSearchClient(
        sys.executable,
        [],
        working_directory=str(Path(__file__).parent),
    )
    error = McpError(
        mcp_types.ErrorData(code=mcp_types.CONNECTION_CLOSED, message="Connection closed")
    )

    async def fail(_payload: dict[str, object], *, trace_id: str) -> dict[str, object]:
        raise error

    monkeypatch.setattr(client, "_call_mcp", fail)

    with pytest.raises(FxDataServiceError, match="Connection closed") as raised:
        client.search("unified_search", "查询 EURUSD 最新价格")
    assert raised.value.code == "FX_DATA_SERVICE_UNAVAILABLE"


def test_mcp_client_uses_stdio_and_only_unified_search() -> None:
    """真实启动 stdio 子进程，验证 domain 别名不会切换到旧工具。"""

    fixture = Path(__file__).parent / "fixtures" / "fake_ai_search_mcp_server.py"
    client = McpAiSearchClient(
        sys.executable,
        [str(fixture)],
        working_directory=str(fixture.parents[2]),
        timeout_seconds=15,
        max_rows=25,
    )

    result = FxDataQueryAgent(client).query(
        "查询 EURUSD 的相关新闻",
        domain="news",
        start_date="2026-08-01",
        end_date="2026-08-18",
        max_rows=7,
    )

    assert result["status"] == "success"
    assert result["data"][0]["query"] == "查询 EURUSD 的相关新闻"
    assert result["data"][0]["start_date"] == "2026-08-01"
    assert result["data"][0]["max_rows"] == 7


def test_mcp_client_forwards_progress_stage_with_trace_id() -> None:
    """主 Agent 接收到 MCP progress 后，向上游发送完整阶段和关联 ID。"""

    fixture = Path(__file__).parent / "fixtures" / "fake_ai_search_mcp_server.py"
    events: list[dict[str, object]] = []
    client = McpAiSearchClient(
        sys.executable,
        [str(fixture)],
        working_directory=str(fixture.parents[2]),
        timeout_seconds=15,
        trace_callback=events.append,
    )

    result = FxDataQueryAgent(client).query("查询 EURUSD 的相关新闻")

    assert result["status"] == "success"
    stage_events = [event for event in events if event["type"] == "data_service.stage"]
    assert len(stage_events) == 1
    assert stage_events[0]["stage"] == "dataset_catalog"
    assert stage_events[0]["output"] == {"dataset_id": "LSEG_NEWS"}
    trace_ids = {event.get("trace_id") for event in events}
    assert len(trace_ids) == 1
    assert next(iter(trace_ids))


def test_mcp_client_reuses_unified_search_for_four_debate_queries() -> None:
    """四个证据计划都通过同一个 MCP 工具执行。"""

    fixture = Path(__file__).parent / "fixtures" / "fake_ai_search_mcp_server.py"
    client = McpAiSearchClient(
        sys.executable,
        [str(fixture)],
        working_directory=str(fixture.parents[2]),
        timeout_seconds=15,
    )

    responses = FxDataQueryAgent(client).retrieve_for_debate(_context())

    assert set(responses) == {"prices", "bars", "macro", "news"}
    assert all(response["status"] == "success" for response in responses.values())
    assert all(response["data"][0]["query"].startswith("查询") for response in responses.values())


def test_evidence_source_maps_provider_metadata_into_raw_snapshot() -> None:
    responses = {
        "prices": {
            "status": "success",
            "data": [{"price_time": "2026-08-18T11:55:00+00:00", "last": "1.10"}],
            "meta": {"provider": "LSEG", "identifier": "EUR="},
        },
        "bars": {
            "status": "success",
            "data": [{"date": "2026-08-17", "open": "1.09", "high": "1.11", "low": "1.08", "close": "1.10"}],
            "meta": {"provider": "LSEG", "identifier": "EUR=", "frequency": "daily"},
        },
        "macro": {
            "status": "success",
            "data": [
                {
                    "data": {"value": "2.4", "forecast_value": "2.3"},
                    "metadata": {"metric_id": "CPI", "release_time": "2026-08-17", "country": "US", "source": "LSEG"},
                }
            ],
            "meta": {"identifier": "EURUSD"},
        },
        "news": {
            "status": "success",
            "data": [
                {
                    "data": {"title": "EURUSD outlook"},
                    "metadata": {"article_id": "n-1", "publish_time": "2026-08-17", "source": "LSEG"},
                }
            ],
            "meta": {},
        },
    }

    class _Client:
        def search(self, _tool, query, **_kwargs):
            return responses[
                "prices"
                if "最新价格" in query
                else "bars"
                if "日线" in query
                else "macro"
                if "宏观" in query
                else "news"
            ]

    snapshot = AiSearchFxEvidenceSource(client=_Client()).load(_context())
    assert snapshot.source_name == "ai_search"
    assert snapshot.prices[0]["source_identifier"] == "EUR="
    assert snapshot.bars[0]["bar_time"] == "2026-08-17"
    assert snapshot.macro[0]["metric_id"] == "CPI"
    assert snapshot.news[0]["article_id"] == "n-1"
    bundle = FxEvidenceFactory().build(_context(), AiSearchFxEvidenceSource(client=_Client()))
    assert bundle.source_name == "ai_search"


def test_evidence_source_maps_legacy_related_macro_role_to_country() -> None:
    """Old MCP rows remain usable when only relationship_role is present."""

    class _Client:
        def search(self, _tool, query, **_kwargs):
            if "最新价格" in query:
                return {"status": "success", "data": []}
            if "日线" in query:
                return {"status": "success", "data": []}
            if "宏观" in query:
                return {
                    "status": "success",
                    "data": [
                        {
                            "metric_id": "EU_CPI_YOY",
                            "relationship_role": "base_currency",
                            "value": "2.4",
                            "forecast_value": "2.3",
                        },
                        {
                            "metric_id": "US_CPI_YOY",
                            "relationship_role": "quote_currency",
                            "value": "3.1",
                            "forecast_value": "3.0",
                        },
                    ],
                }
            return {"status": "success", "data": []}

    snapshot = AiSearchFxEvidenceSource(client=_Client()).load(_context())
    assert {row["country"] for row in snapshot.macro} == {"EU", "US"}


def test_top_level_query_tool_is_opt_in(monkeypatch) -> None:
    from src import tools as tools_package

    monkeypatch.setenv("FX_DATA_SERVICE_ENABLED", "1")
    monkeypatch.setenv("FX_DATA_SERVICE_URL", "http://127.0.0.1:8011")
    previous_cache = tools_package._SUBCLASSES_CACHE
    try:
        tools_package._SUBCLASSES_CACHE = None
        registry = tools_package.build_registry()
    finally:
        tools_package._SUBCLASSES_CACHE = previous_cache
    assert "query_fx_data" in registry.tool_names
