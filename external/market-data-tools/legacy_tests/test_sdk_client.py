"""Python SDK 的协议、路由和 SSE 解析测试。"""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sys
import urllib.error

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "sdk"))

from icbc_ai_search import (  # noqa: E402
    AISearchClient,
    AISearchHTTPError,
    SearchOptions,
    SearchRoute,
)


class FakeResponse:
    """模拟 urllib 响应，支持普通 JSON 和逐行 SSE 两种读取方式。"""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.lines = iter(body.splitlines(keepends=True))
        self.closed = False

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read(self) -> bytes:
        return self.body

    def readline(self) -> bytes:
        return next(self.lines, b"")

    def close(self) -> None:
        self.closed = True


def response_payload(route: str = "latest_prices") -> dict[str, object]:
    """构造最小业务响应，覆盖统一响应和显式路线响应的公共字段。"""

    result_key = {
        "latest_prices": "price_result",
        "market_bars": "market_bars_result",
        "macro_observations": "macro_observations_result",
        "news_articles": "news_result",
    }[route]
    return {
        "route": route,
        result_key: {"status": "resolved", "rows": [{"value": "1"}], "row_count": 1},
    }


def test_unified_search_posts_without_route_and_wraps_business_data() -> None:
    """统一方法不能自行指定路线，并且应提取对应路线的业务结果。"""

    calls: list[tuple[str, dict[str, object]]] = []

    def opener(request, timeout):
        calls.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return FakeResponse(json.dumps(response_payload()).encode("utf-8"))

    client = AISearchClient("http://test.local/", opener=opener)
    result = client.search("查询 EURUSD 的最新价格", limit=3)

    assert result.route is SearchRoute.LATEST_PRICES
    assert result.is_resolved
    assert result.row_count == 1
    assert calls[0][0] == "http://test.local/api/search/unified"
    assert "route" not in calls[0][1]
    assert calls[0][1]["query"] == "查询 EURUSD 的最新价格"


def test_unified_search_reads_dataset_driven_execution_response() -> None:
    """新统一协议没有 route 时，SDK 仍能读取目录和 execution 结果。"""

    payload = {
        "status": "success",
        "query": "查询 EURUSD 的最新价格",
        "dataset_resolution": {
            "status": "resolved",
            "dataset_id": "LSEG_SPOT_PRICE",
            "storage_table_name": "latest_prices",
        },
        "routing": {
            "mode": "dataset_catalog",
            "dataset_id": "LSEG_SPOT_PRICE",
            "storage_table_name": "latest_prices",
            "adapter": "latest_prices",
        },
        "execution": {
            "status": "resolved",
            "adapter": "latest_prices",
            "rows": [{"last": "1.1528"}],
            "row_count": 1,
        },
    }

    def opener(request, timeout):
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    result = AISearchClient("http://test.local", opener=opener).search("查询 EURUSD 的最新价格")

    assert result.route is SearchRoute.LATEST_PRICES
    assert result.dataset_id == "LSEG_SPOT_PRICE"
    assert result.storage_table_name == "latest_prices"
    assert result.adapter == "latest_prices"
    assert result.execution is not None
    assert result.data == result.execution
    assert result.row_count == 1


def test_sdk_reads_compact_public_response() -> None:
    """SDK 普通查询应直接读取公开协议中的业务 data 数组。"""

    payload = {
        "status": "success",
        "data": [{"price_time": "2026-08-01T03:53:45+08:00", "last": "1.1528"}],
    }

    def opener(request, timeout):
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    result = AISearchClient("http://test.local", opener=opener).search(
        "查询 EURUSD 的最新价格"
    )

    assert result.route is None
    assert result.is_resolved
    assert result.data == payload["data"]
    assert result.row_count == 1
    assert result.execution is None


def test_explicit_route_posts_route_enum_and_options() -> None:
    """显式路线方法应只提交受控枚举和查询参数。"""

    captured: dict[str, object] = {}

    def opener(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(json.dumps(response_payload("market_bars")).encode("utf-8"))

    client = AISearchClient("http://test.local", opener=opener)
    result = client.market_bars(
        "查询 EURUSD 最近一个月的日 K 线",
        row_limit=30,
        start_date="2026-07-01",
        end_date="2026-07-31",
    )

    assert result.route is SearchRoute.MARKET_BARS
    assert captured["route"] == "market_bars"
    assert captured["row_limit"] == 30
    assert captured["start_date"] == "2026-07-01"
    assert captured["end_date"] == "2026-07-31"


def test_search_stream_yields_stage_result_and_done_events() -> None:
    """SSE 解析必须按空行切分事件，并把 stage/result 转成便捷对象。"""

    sse = (
        'event: stage\ndata: {"stage":"query_parse","status":"completed",'
        '"input":{"query":"EURUSD"},"output":{},"duration_ms":12,"error":null}\n\n'
        'event: result\ndata: ' + json.dumps(response_payload(), ensure_ascii=False) + '\n\n'
        'event: done\ndata: {"finished_at":"2026-08-11"}\n\n'
    ).encode("utf-8")

    def opener(request, timeout):
        return FakeResponse(sse)

    events = list(AISearchClient("http://test.local", opener=opener).search_stream("EURUSD"))

    assert [event.type for event in events] == ["stage", "result", "done"]
    assert events[0].stage is not None
    assert events[0].stage.stage == "query_parse"
    assert events[1].result is not None
    assert events[1].result.is_resolved


def test_options_reject_reversed_dates_and_mixed_options() -> None:
    """客户端在发请求前拒绝确定性的参数错误。"""

    with pytest.raises(ValueError, match="start_date"):
        SearchOptions(start_date="2026-08-02", end_date="2026-08-01").to_payload("EURUSD")

    client = AISearchClient("http://test.local", opener=lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="不能同时使用"):
        client.search("EURUSD", options=SearchOptions(), limit=3)


def test_http_error_exposes_status_and_structured_payload() -> None:
    """非 2xx 响应应保留后端结构化错误，便于上层诊断。"""

    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            "service unavailable",
            {},
            BytesIO(b'{"error_type":"RuntimeError","message":"model failed"}'),
        )

    with pytest.raises(AISearchHTTPError) as error:
        AISearchClient("http://test.local", opener=opener).search("EURUSD")

    assert error.value.status_code == 503
    assert error.value.payload["error_type"] == "RuntimeError"
