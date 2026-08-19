"""FastAPI 五个工具接口的请求边界测试。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))

from backend.main import app  # noqa: E402
from backend import main  # noqa: E402


def test_tool_endpoint_rejects_route_and_sql(monkeypatch) -> None:
    """正式 Agent 接口不能接收 route、表名或 SQL。"""

    monkeypatch.setattr(
        main,
        "_public_result",
        lambda _name, _request: {"status": "success", "data": []},
    )
    client = TestClient(app)
    response = client.post(
        "/tools/unified_search",
        json={"query": "查询 EURUSD 最新价格", "route": "latest_prices"},
    )
    assert response.status_code == 422


def test_all_five_tool_paths_are_registered(monkeypatch) -> None:
    """五个 HTTP 工具路径均使用统一精简响应。"""

    monkeypatch.setattr(
        main,
        "_public_result",
        lambda _name, _request: {"status": "success", "data": []},
    )
    client = TestClient(app)
    paths = (
        "/tools/unified_search",
        "/tools/latest_prices_search",
        "/tools/market_bars_search",
        "/tools/macro_observations_search",
        "/tools/news_articles_search",
    )
    for path in paths:
        response = client.post(path, json={"query": "测试"})
        assert response.status_code == 200
        assert response.json() == {"status": "success", "data": []}
