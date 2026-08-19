"""Embedding 请求协议测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))

from backend.ai_search import generate_embeddings  # noqa: E402


class _FakeResponse:
    """提供 urllib 响应对象所需的最小上下文管理器接口。"""

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {"data": [{"index": 0, "embedding": [0.1] * 2048}]}
        ).encode("utf-8")


def test_request_embeddings_sends_requested_dimension(monkeypatch) -> None:
    """确认 Qwen 模型请求不会遗漏数据库要求的 2048 维参数。"""

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: int):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(generate_embeddings.urllib.request, "urlopen", fake_urlopen)
    vectors = generate_embeddings.request_embeddings(
        ["test"],
        "test-key",
        "Qwen/Qwen3-Embedding-8B",
        "https://example.test/v1/embeddings",
        2048,
    )

    assert captured["payload"] == {
        "model": "Qwen/Qwen3-Embedding-8B",
        "input": ["test"],
        "dimensions": 2048,
    }
    assert len(vectors) == 1
    assert len(vectors[0]) == 2048
