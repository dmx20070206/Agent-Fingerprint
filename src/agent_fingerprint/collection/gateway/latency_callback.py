"""Optional LiteLLM callback that records request-level timing events.

LiteLLM loads this module from ``litellm_settings.callbacks`` in the proxy
process.  The callback API has changed slightly between LiteLLM releases, so
all hooks are intentionally defensive and accept ``*args``/``**kwargs``.
The callback records request timing and structural metadata only. Prompts,
responses, credentials, and authorization headers are never written to disk.
The callback also applies provider-specific compatibility fixes before
LiteLLM dispatches a request.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # pragma: no cover - depends on the optional LiteLLM installation
    from litellm.integrations.custom_logger import CustomLogger
except ImportError:  # pragma: no cover - allows config inspection without LiteLLM

    class CustomLogger:  # type: ignore[no-redef]
        pass


_LOCK = threading.Lock()
_STARTS: dict[str, tuple[float, str | None, int]] = {}
_THREAD_STARTS: dict[int, list[str]] = {}
_TERMINAL: dict[str, float] = {}
_TERMINAL_CONTEXT: dict[tuple[int, str | None], tuple[str, float]] = {}


def _log_path() -> Path:
    return Path(os.environ.get("AGENT_FINGERPRINT_GATEWAY_LOG", "gateway_events.jsonl")).expanduser().resolve()


def _request_id(kwargs: dict[str, Any]) -> str:
    for key in ("_agent_fp_request_id", "request_id", "litellm_trace_id", "trace_id", "call_id"):
        value = kwargs.get(key)
        if value:
            return str(value)
    return f"local-{time.time_ns()}"


def _model(kwargs: dict[str, Any], fallback: Any = None) -> str | None:
    value = kwargs.get("model") or kwargs.get("deployment") or fallback
    return str(value) if value is not None else None


def _is_deepseek_route(model: str | None) -> bool:
    """Return whether *model* is one of our DeepSeek public/provider routes."""

    if not model:
        return False
    value = model.strip().lower()
    # LiteLLM may pass either the public alias (deepseek-chat) or the provider
    # model (deepseek/deepseek-chat) to callbacks.
    return value.startswith("deepseek-") or value.startswith("deepseek/")


def _apply_route_compatibility(model: str | None, kwargs: dict[str, Any]) -> list[str]:
    """Apply request fixes scoped to a provider route.

    Browser-use currently sends OpenAI Structured Outputs (``json_schema``)
    for its action union.  DeepSeek's Chat Completions endpoint rejects that
    response format, while it can still consume the action definitions as
    normal function tools.  Remove only ``response_format`` on DeepSeek
    routes and leave ``tools``/``tool_choice`` untouched.  Other providers
    retain the original request unchanged.

    Returns names of changed fields for diagnostics.
    """

    if not _is_deepseek_route(model) or "response_format" not in kwargs:
        return []
    kwargs.pop("response_format", None)
    return ["response_format"]


def _usage(response_obj: Any) -> dict[str, Any]:
    usage = getattr(response_obj, "usage", None)
    if usage is None and isinstance(response_obj, dict):
        usage = response_obj.get("usage")
    if usage is None:
        return {}
    if isinstance(usage, dict):
        source = usage
    else:
        source = {
            name: getattr(usage, name)
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
            if hasattr(usage, name)
        }
    return {key: value for key, value in source.items() if isinstance(value, (int, float))}


def _write(event: dict[str, Any]) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {key: _sanitize(value) for key, value in event.items()}
    safe.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    with _LOCK:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe, ensure_ascii=False, default=str) + "\n")


def _sanitize(value: Any) -> Any:
    """Recursively redact credentials while retaining model conversation text."""

    forbidden = {"api_key", "authorization", "x-api-key"}
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items() if str(key).lower() not in forbidden}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _message_shape(messages: Any) -> dict[str, Any]:
    """Return modality-only metadata without persisting prompt/image content."""

    if not isinstance(messages, (list, tuple)):
        return {"message_count": None, "content_types": [], "image_count": 0}
    content_types: list[str] = []
    image_count = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            content_types.append("text")
            continue
        if not isinstance(content, (list, tuple)):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "unknown")
            content_types.append(kind)
            if kind in {"image_url", "input_image"}:
                image_count += 1
    return {
        "message_count": len(messages),
        "content_types": content_types,
        "image_count": image_count,
    }


def _response_shape(response_obj: Any) -> dict[str, Any]:
    """Summarize model response control flow without recording response text."""

    raw = response_obj
    if not isinstance(raw, dict):
        model_dump = getattr(raw, "model_dump", None)
        if callable(model_dump):
            try:
                raw = model_dump()
            except Exception:
                raw = response_obj
        if not isinstance(raw, dict):
            as_dict = getattr(raw, "dict", None)
            if callable(as_dict):
                try:
                    raw = as_dict()
                except Exception:
                    raw = response_obj
    choices = raw.get("choices") if isinstance(raw, dict) else getattr(raw, "choices", None)
    if not isinstance(choices, (list, tuple)):
        return {"choice_count": None, "finish_reasons": [], "tool_call_counts": []}
    finish_reasons: list[Any] = []
    tool_call_counts: list[int] = []
    for choice in choices:
        if isinstance(choice, dict):
            finish_reasons.append(choice.get("finish_reason"))
            message = choice.get("message") or {}
        else:
            finish_reasons.append(getattr(choice, "finish_reason", None))
            message = getattr(choice, "message", None)
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
        tool_call_counts.append(len(tool_calls) if isinstance(tool_calls, (list, tuple)) else 0)
    return {
        "choice_count": len(choices),
        "finish_reasons": finish_reasons,
        "tool_call_counts": tool_call_counts,
    }


def _mark_start(request_id: str, started: float, model: str | None) -> None:
    with _LOCK:
        thread_id = threading.get_ident()
        previous = _STARTS.get(request_id)
        if previous is not None:
            previous_queue = _THREAD_STARTS.get(previous[2], [])
            if request_id in previous_queue:
                previous_queue.remove(request_id)
        _STARTS[request_id] = (started, model, thread_id)
        _THREAD_STARTS.setdefault(thread_id, []).append(request_id)


def _pop_start(request_id: str, model: str | None = None) -> tuple[str, float, bool] | None:
    """Remove a pending request start.

    The third return value says whether a pending start was found.  LiteLLM
    can invoke two terminal hooks for one request and, in some versions,
    passes a copied ``kwargs`` mapping without our private request id.  That
    distinction lets the caller suppress only the second, unmatched hook
    while retaining two genuinely fast, concurrent requests.
    """

    with _LOCK:
        value = _STARTS.pop(request_id, None)
        if value is not None:
            started, _model_name, thread_id = value
            queue = _THREAD_STARTS.get(thread_id, [])
            if request_id in queue:
                queue.remove(request_id)
            return request_id, started, True
        # Some proxy versions pass a copied kwargs mapping to the terminal
        # hook, losing the private request id.  Match the newest pending call
        # on this thread/model as a defensive fallback.
        thread_id = threading.get_ident()
        queue = _THREAD_STARTS.get(thread_id, [])
        for candidate in reversed(queue):
            candidate_value = _STARTS.get(candidate)
            if candidate_value is None:
                continue
            started, candidate_model, _ = candidate_value
            if model is None or candidate_model is None or candidate_model == model:
                _STARTS.pop(candidate, None)
                queue.remove(candidate)
                return candidate, started, True
        return None


def _emit_terminal(request_id: str, event: dict[str, Any], *, unmatched_start: bool = False) -> None:
    # Some LiteLLM releases invoke both log_post_api_call and
    # log_success_event.  Deduplicate by the stable request id so one model
    # request produces one end/error record.
    with _LOCK:
        now = time.monotonic()
        previous = _TERMINAL.get(request_id)
        if previous is not None and now - previous < 60:
            return
        context = (threading.get_ident(), event.get("model"))
        previous_context = _TERMINAL_CONTEXT.get(context)
        # If a proxy copied kwargs and called both terminal hooks, the second
        # invocation has neither the private id nor a pending start.  Suppress
        # only that narrow same-thread/same-model window.  A distinct request
        # with a pending start sets ``unmatched_start=False`` and is retained
        # even when it completes within the same millisecond.
        if unmatched_start and previous_context and now - previous_context[1] < 1:
            return
        _TERMINAL[request_id] = now
        _TERMINAL_CONTEXT[context] = (request_id, now)
        if len(_TERMINAL) > 10000:
            cutoff = now - 60
            for key, value in list(_TERMINAL.items()):
                if value < cutoff:
                    _TERMINAL.pop(key, None)
    _write(event)


class AgentFingerprintCallback(CustomLogger):
    """Record ``request_start``/``request_end``/``request_error`` events."""

    def log_pre_api_call(self, model, messages, kwargs):  # noqa: ANN001
        # Request compatibility is handled in the Browser-use model adapter,
        # where the final structured-output request is constructed.  Keep
        # this callback observational so it cannot diverge from LiteLLM's
        # internal request state.
        changed_fields: list[str] = []
        data = dict(kwargs or {})
        request_id = _request_id(data)

        if isinstance(kwargs, dict):
            kwargs.setdefault("_agent_fp_request_id", request_id)

        started = time.perf_counter_ns()
        _mark_start(request_id, started, _model(data, model))

        response_format = data.get("response_format")
        tools = data.get("tools")
        tool_choice = data.get("tool_choice")
        request_shape = {
            "response_format_type": (
                response_format.get("type") if isinstance(response_format, dict) else None
            ),
            "tool_count": len(tools) if isinstance(tools, (list, tuple)) else 0,
            "tool_choice_type": (
                tool_choice.get("type")
                if isinstance(tool_choice, dict)
                else "string"
                if isinstance(tool_choice, str)
                else None
            ),
            "message_shape": _message_shape(messages),
        }

        _write(
            {
                "event": "request_start",
                "request_id": request_id,
                "model": _model(data, model),
                "started_ns": started,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "request_shape": request_shape,
                "route_compatibility": {
                    "applied": bool(changed_fields),
                    "removed_fields": changed_fields,
                },
            }
        )

    async def async_log_pre_api_call(self, model, messages, kwargs):  # noqa: ANN001
        """Async counterpart used by LiteLLM's async proxy paths."""

        self.log_pre_api_call(model, messages, kwargs)

    def log_post_api_call(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        data = dict(kwargs or {})
        request_id = _request_id(data)
        matched = _pop_start(request_id, _model(data))
        if matched:
            request_id, started, _ = matched
        else:
            started = None
        ended = time.perf_counter_ns()
        latency_ms = ((ended - started) / 1_000_000) if started else _duration_ms(start_time, end_time)
        _emit_terminal(
            request_id,
            {
                "event": "request_end",
                "request_id": request_id,
                "model": _model(data),
                "ended_ns": ended,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": latency_ms,
                "usage": _usage(response_obj),
                "response_shape": _response_shape(response_obj),
            },
            unmatched_start=matched is None,
        )

    async def async_log_post_api_call(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        self.log_post_api_call(kwargs, response_obj, start_time, end_time)

    def log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        self.log_post_api_call(kwargs, response_obj, start_time, end_time)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        data = dict(kwargs or {})
        request_id = _request_id(data)
        matched = _pop_start(request_id, _model(data))
        if matched:
            request_id, started, _ = matched
        else:
            started = None
        ended = time.perf_counter_ns()
        _emit_terminal(
            request_id,
            {
                "event": "request_error",
                "request_id": request_id,
                "model": _model(data),
                "ended_ns": ended,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": ((ended - started) / 1_000_000) if started else _duration_ms(start_time, end_time),
                "response_shape": _response_shape(response_obj),
            },
            unmatched_start=matched is None,
        )

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        self.log_failure_event(kwargs, response_obj, start_time, end_time)


def _duration_ms(start_time: Any, end_time: Any) -> float | None:
    try:
        return (end_time - start_time).total_seconds() * 1000
    except (AttributeError, TypeError):
        return None


proxy_handler_instance = AgentFingerprintCallback()
