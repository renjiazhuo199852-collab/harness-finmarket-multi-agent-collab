"""OpenAI-compatible Responses adapter tests for GPT-5.6 Luna."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from src.agent.context import ContextBuilder
from src.providers import llm as llm_mod
from src.providers.chat import ChatLLM
from src.providers.openai_codex import (
    _events_from_lines,
    _message_chunks_from_events,
)
from src.providers.openai_responses import (
    OpenAIResponsesLLM,
    ResponsesStreamError,
    responses_endpoint,
)


def _adapter(**overrides) -> OpenAIResponsesLLM:
    values = {
        "model": "gpt-5.6-luna",
        "api_key": "secret-test-key",
        "base_url": "https://ai.example/v1",
        "reasoning_effort": "xhigh",
        "store": False,
        "custom_headers": {"x-openai-actor-authorization": "local-image-extension"},
    }
    values.update(overrides)
    return OpenAIResponsesLLM(**values)


def test_responses_endpoint_appends_once() -> None:
    assert responses_endpoint("https://ai.example/v1") == (
        "https://ai.example/v1/responses"
    )
    assert responses_endpoint("https://ai.example/v1/responses/") == (
        "https://ai.example/v1/responses"
    )


def test_luna_body_maps_reasoning_storage_headers_and_tools() -> None:
    adapter = _adapter().bind_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "get_fx_evidence_manifest",
                    "description": "Read manifest",
                    "parameters": {
                        "type": "object",
                        "properties": {"evidence_context_id": {"type": "string"}},
                        "required": ["evidence_context_id"],
                    },
                },
            }
        ]
    )

    body = adapter._body(
        [
            {"role": "system", "content": "Use frozen evidence."},
            {"role": "user", "content": "Analyze EURUSD."},
        ],
        stream=True,
    )

    assert adapter.responses_url == "https://ai.example/v1/responses"
    assert adapter._headers()["x-openai-actor-authorization"] == (
        "local-image-extension"
    )
    assert body["model"] == "gpt-5.6-luna"
    assert body["store"] is False
    assert body["stream"] is True
    assert body["reasoning"] == {"effort": "xhigh"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["tools"][0]["name"] == "get_fx_evidence_manifest"
    assert "temperature" not in body


def test_encrypted_reasoning_items_are_replayed_after_tool_calls() -> None:
    events = list(
        _events_from_lines(
            [
                'data: {"type":"response.output_item.done","item":{"type":"reasoning","id":"rs_1","encrypted_content":"enc_1","summary":[]}}',
                "",
                'data: {"type":"response.output_item.added","item":{"type":"function_call","call_id":"call_1","id":"fc_1","name":"get_fx_evidence_manifest","arguments":""}}',
                "",
                'data: {"type":"response.function_call_arguments.done","call_id":"call_1","arguments":"{\\"evidence_context_id\\":\\"ctx-1\\"}"}',
                "",
                'data: {"type":"response.output_item.done","item":{"type":"function_call","call_id":"call_1"}}',
                "",
                'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}',
                "",
            ]
        )
    )
    accumulated = None
    for chunk in _message_chunks_from_events(events):
        accumulated = chunk if accumulated is None else accumulated + chunk
    assert accumulated is not None

    response = ChatLLM._parse_response(accumulated)
    assistant = ContextBuilder.format_assistant_tool_calls(
        response.tool_calls,
        provider_state=response.provider_state,
    )
    body = _adapter()._body(
        [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Analyze"},
            assistant,
            {
                "role": "tool",
                "tool_call_id": response.tool_calls[0].id,
                "content": "{}",
            },
        ],
        stream=True,
    )

    reasoning = next(item for item in body["input"] if item.get("type") == "reasoning")
    assert reasoning["encrypted_content"] == "enc_1"
    assert response.usage_metadata == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_build_llm_selects_responses_wire_adapter() -> None:
    llm_mod._dotenv_loaded = True
    env = {
        "LANGCHAIN_PROVIDER": "openai",
        "LANGCHAIN_MODEL_NAME": "gpt-5.6-luna",
        "LANGCHAIN_WIRE_API": "responses",
        "LANGCHAIN_REASONING_EFFORT": "xhigh",
        "OPENAI_API_KEY": "secret-test-key",
        "OPENAI_BASE_URL": "https://ai.example/v1",
        "OPENAI_DISABLE_RESPONSE_STORAGE": "true",
        "OPENAI_RESPONSES_HTTP_HEADERS": json.dumps(
            {"x-openai-actor-authorization": "local-image-extension"}
        ),
    }
    with patch.dict(os.environ, env, clear=True):
        adapter = llm_mod.build_llm()

    assert isinstance(adapter, OpenAIResponsesLLM)
    assert adapter.model == "gpt-5.6-luna"
    assert adapter.reasoning_effort == "xhigh"
    assert adapter.store is False


def test_stream_posts_responses_payload_and_custom_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class _Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def iter_lines():
            return iter(
                [
                    'data: {"type":"response.output_text.delta","delta":"OK"}',
                    "",
                    'data: {"type":"response.completed","response":{"status":"completed"}}',
                    "",
                ]
            )

    class _Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def stream(self, method, url, **kwargs):
            captured.update(method=method, url=url, request=kwargs)
            return _Response()

    import src.providers.openai_responses as responses_mod

    monkeypatch.setattr(responses_mod.httpx, "Client", _Client)
    chunks = list(_adapter().stream([{"role": "user", "content": "ping"}]))

    assert "".join(chunk.content for chunk in chunks) == "OK"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://ai.example/v1/responses"
    assert captured["request"]["headers"]["Authorization"] == ("Bearer secret-test-key")
    assert captured["request"]["headers"]["x-openai-actor-authorization"] == (
        "local-image-extension"
    )
    assert captured["request"]["json"]["reasoning"]["effort"] == "xhigh"


def test_http_error_preserves_status_for_retry_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        status_code = 404

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def read():
            return b"model not found"

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def stream(*args, **kwargs):
            return _Response()

    import src.providers.openai_responses as responses_mod

    monkeypatch.setattr(responses_mod.httpx, "Client", _Client)
    with pytest.raises(ResponsesStreamError) as exc_info:
        list(_adapter().stream([{"role": "user", "content": "ping"}]))
    assert exc_info.value.status_code == 404


def test_custom_headers_cannot_override_authorization() -> None:
    with pytest.raises(ValueError, match="cannot override Authorization"):
        _adapter(custom_headers={"Authorization": "wrong"})
