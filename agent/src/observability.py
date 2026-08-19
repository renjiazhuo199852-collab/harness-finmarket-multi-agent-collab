"""Small process-local observation seam for nested SDK and database calls."""

from __future__ import annotations

import inspect
import json
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Iterator

from src.tools.redaction import redact_payload

ObservationSink = Callable[[dict[str, Any]], None]
_SINK: ContextVar[ObservationSink | None] = ContextVar(
    "market_observation_sink",
    default=None,
)


@contextmanager
def observation_scope(sink: ObservationSink | None) -> Iterator[None]:
    """Route nested observations to ``sink`` for the current execution context."""
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


class ObservationSpan:
    """Emit paired started/completed/failed events around one operation."""

    def __init__(self, layer: str, operation: str, inputs: Any) -> None:
        self._layer = layer
        self._operation = operation
        self._input = _safe_payload(inputs)
        self._call_id = f"{layer}-{uuid.uuid4().hex[:12]}"
        self._started = 0.0
        self._output: Any = None

    def __enter__(self) -> "ObservationSpan":
        self._started = time.monotonic()
        _emit(
            f"{self._layer}_started",
            {
                "call_id": self._call_id,
                "operation": self._operation,
                "input": self._input,
            },
        )
        return self

    def set_output(self, output: Any) -> None:
        self._output = _safe_payload(output)

    def __exit__(self, exc_type, exc, _traceback) -> None:
        elapsed_ms = max(0, int((time.monotonic() - self._started) * 1000))
        common = {
            "call_id": self._call_id,
            "operation": self._operation,
            "elapsed_ms": elapsed_ms,
        }
        if exc is None:
            _emit(
                f"{self._layer}_completed",
                {**common, "output": self._output},
            )
        else:
            _emit(
                f"{self._layer}_failed",
                {**common, "error": str(exc)},
            )


def observation_span(layer: str, operation: str, inputs: Any) -> ObservationSpan:
    """Create one observation span without changing the wrapped API."""
    return ObservationSpan(layer, operation, inputs)


def observed_sdk(operation: str):
    """Decorate a Reader method with structured SDK input/output events."""

    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            inputs = {
                key: value for key, value in bound.arguments.items() if key != "self"
            }
            with observation_span("sdk_call", operation, inputs) as span:
                result = function(*args, **kwargs)
                span.set_output(_summarize_result(result))
                return result

        return wrapped

    return decorate


def _emit(event_type: str, data: dict[str, Any]) -> None:
    sink = _SINK.get()
    if sink is None:
        return
    sink({"type": event_type, "data": _safe_payload(data)})


def _summarize_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return _safe_payload(result)
    summary: dict[str, Any] = {}
    for key, value in result.items():
        if isinstance(value, list):
            summary[key] = {
                "count": len(value),
                "rows": _safe_payload(value[:5]),
                "truncated": len(value) > 5,
            }
        else:
            summary[key] = _safe_payload(value)
    return summary


def _safe_payload(value: Any) -> Any:
    """Redact secrets and coerce database-native values to JSON-safe data."""
    redacted = redact_payload(value)
    return json.loads(json.dumps(redacted, ensure_ascii=False, default=str))
