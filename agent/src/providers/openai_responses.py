"""Generic API-key adapter for OpenAI-compatible Responses endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from src.providers.openai_codex import (
    CodexAIMessage,
    _convert_messages,
    _convert_tools,
    _events_from_lines,
    _message_chunks_from_events,
)

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


class ResponsesStreamError(RuntimeError):
    """HTTP failure carrying a status code for retry classification."""

    def __init__(self, status_code: int, detail: Any) -> None:
        self.status_code = int(status_code)
        super().__init__(
            f"OpenAI Responses HTTP {self.status_code}: {str(detail)[:500]}"
        )


def responses_endpoint(base_url: str) -> str:
    """Resolve an OpenAI-compatible base URL to its Responses endpoint."""
    value = (base_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Responses base URL must be an absolute HTTP(S) URL")
    return value if parsed.path.endswith("/responses") else f"{value}/responses"


class OpenAIResponsesLLM:
    """Small ChatLLM-compatible interface over the Responses wire protocol."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout: int = 120,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str | None = None,
        reasoning_context: str = "auto",
        store: bool = False,
        text_verbosity: str = "medium",
        custom_headers: dict[str, str] | None = None,
    ) -> None:
        if httpx is None:
            raise RuntimeError("Responses adapter requires httpx")
        if not api_key:
            raise ValueError("Responses adapter requires an API key")
        if reasoning_context not in {"auto", "current_turn", "all_turns"}:
            raise ValueError("invalid Responses reasoning context")
        if text_verbosity not in {"low", "medium", "high"}:
            raise ValueError("invalid Responses text verbosity")
        headers = dict(custom_headers or {})
        if any(name.lower() == "authorization" for name in headers):
            raise ValueError("custom Responses headers cannot override Authorization")
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in headers.items()
        ):
            raise ValueError("custom Responses headers must be string pairs")

        self.model = model
        self.api_key = api_key
        self.responses_url = responses_endpoint(base_url)
        self.timeout = timeout
        self.tools = tools or []
        self.reasoning_effort = (reasoning_effort or "").strip().lower() or None
        self.reasoning_context = reasoning_context
        self.store = bool(store)
        self.text_verbosity = text_verbosity
        self.custom_headers = headers

    def bind_tools(self, tools: list[dict[str, Any]]) -> "OpenAIResponsesLLM":
        """Return an adapter sharing configuration with a new tool schema."""
        return OpenAIResponsesLLM(
            model=self.model,
            api_key=self.api_key,
            base_url=self.responses_url,
            timeout=self.timeout,
            tools=tools,
            reasoning_effort=self.reasoning_effort,
            reasoning_context=self.reasoning_context,
            store=self.store,
            text_verbosity=self.text_verbosity,
            custom_headers=self.custom_headers,
        )

    def _body(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        instructions, input_items = _convert_messages(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "store": self.store,
            "stream": stream,
            "instructions": instructions,
            "input": input_items,
            "text": {"verbosity": self.text_verbosity},
        }
        if not self.store:
            body["include"] = ["reasoning.encrypted_content"]
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
            if self.reasoning_context != "auto":
                body["reasoning"]["context"] = self.reasoning_context
        tools = _convert_tools(self.tools)
        if tools:
            body.update(
                tools=tools,
                tool_choice="auto",
                parallel_tool_calls=True,
            )
        return body

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "accept": "text/event-stream",
            "content-type": "application/json",
            **self.custom_headers,
        }

    def stream(
        self,
        messages: list[dict[str, Any]],
        config: Optional[dict[str, Any]] = None,
    ) -> Iterable[CodexAIMessage]:
        timeout = (config or {}).get("timeout") or self.timeout
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            trust_env=True,
        ) as client:
            with client.stream(
                "POST",
                self.responses_url,
                headers=self._headers(),
                json=self._body(messages, stream=True),
            ) as response:
                if response.status_code != 200:
                    raw = response.read().decode("utf-8", "ignore")
                    raise ResponsesStreamError(response.status_code, raw)
                yield from _message_chunks_from_events(
                    _events_from_lines(response.iter_lines())
                )

    def invoke(
        self,
        messages: list[dict[str, Any]],
        config: Optional[dict[str, Any]] = None,
    ) -> CodexAIMessage:
        accumulated: CodexAIMessage | None = None
        for chunk in self.stream(messages, config=config):
            accumulated = chunk if accumulated is None else accumulated + chunk
        return accumulated or CodexAIMessage()

    async def ainvoke(
        self,
        messages: list[dict[str, Any]],
        config: Optional[dict[str, Any]] = None,
    ) -> CodexAIMessage:
        return await asyncio.to_thread(self.invoke, messages, config)
