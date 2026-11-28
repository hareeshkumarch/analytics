from email.utils import parsedate_to_datetime
import json
import logging
from typing import Any, Callable, Generator

import httpx

from app.domain.errors import ProviderError
from app.domain.limits import LOG_EXCERPT_CHARS

logger = logging.getLogger(__name__)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
        from datetime import datetime, timezone
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def classify_http_error(
    status_code: int, body_text: str, provider_name: str, retry_after: str | None = None
) -> ProviderError:
    logger.warning("%s returned HTTP %s: %s", provider_name, status_code, body_text[:LOG_EXCERPT_CHARS])
    if status_code == 429 or status_code >= 500:
        error = ProviderError(
            f"{provider_name} is temporarily unavailable (HTTP {status_code})",
            code="provider_transient_error",
            retryable=True,
        )
        error.retry_after = parse_retry_after(retry_after)
        return error
    if status_code in (401, 403):
        return ProviderError(
            f"{provider_name} authentication failed ({status_code})",
            code="provider_auth_error",
            retryable=False,
            remediation="Verify provider API key in Settings.",
        )
    return ProviderError(
        f"{provider_name} rejected the request (HTTP {status_code})",
        code="provider_invalid_request",
        retryable=False,
    )


_FAILED_STATUSES = frozenset({"failed", "cancelled", "canceled", "expired"})
_TRUNCATED_REASONS = frozenset({"length", "max_tokens", "max_output_tokens", "MAX_TOKENS"})


def read_json_body(response: httpx.Response, provider_name: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        logger.warning("%s returned a non-JSON body: %s", provider_name, response.text[:LOG_EXCERPT_CHARS])
        raise ProviderError(
            f"{provider_name} returned a response that is not JSON", code="provider_malformed_response", retryable=True
        ) from exc
    if not isinstance(data, dict):
        raise ProviderError(f"{provider_name} returned an unexpected response shape", code="provider_malformed_response", retryable=True)
    if data.get("error") or data.get("type") == "error":
        logger.warning("%s returned an error inside a successful response: %s", provider_name, json.dumps(data)[:LOG_EXCERPT_CHARS])
        raise ProviderError(f"{provider_name} reported an error", code="provider_error_body", retryable=True)
    status = str(data.get("status") or "").lower()
    if status in _FAILED_STATUSES:
        raise ProviderError(f"{provider_name} reported the request as {status}", code="provider_failed_response", retryable=True)
    if status == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason") or "unknown reason"
        usage = data.get("usage") or {}
        raise_if_truncated(
            str(reason), provider_name, always=True, usage=(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))
        )
    return data


def raise_if_truncated(
    reason: str | None, provider_name: str, *, always: bool = False, usage: tuple[int, int] | None = None
) -> None:
    if always or (reason or "") in _TRUNCATED_REASONS:
        error = ProviderError(
            f"{provider_name} stopped before finishing the reply ({reason})", code="provider_incomplete", retryable=False
        )
        error.billed_tokens = usage if usage and any(usage) else None
        raise error


def parse_strict_json(text: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Structured output was not valid JSON: {exc}", code="schema_mismatch", retryable=True) from exc
    if not isinstance(obj, dict):
        raise ProviderError("Structured output must be a JSON object", code="schema_mismatch", retryable=True)
    if schema:
        required = schema.get("required", [])
        missing = [k for k in required if k not in obj]
        if missing:
            raise ProviderError(
                f"Structured output missing required fields: {missing}",
                code="schema_mismatch",
                retryable=True,
            )
    return obj


def iter_sse_data(response: httpx.Response) -> Generator[dict[str, Any], None, None]:
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


StreamState = dict[str, Any]
STREAM_UNSUPPORTED = "stream_unsupported"


def stream_sse_text(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
    transport: httpx.BaseTransport | None,
    provider_name: str,
    read_event: Callable[[dict[str, Any], StreamState], str | None],
) -> Generator[str, None, tuple[str, StreamState]]:
    state: StreamState = {"input_tokens": 0, "output_tokens": 0, "finish_reason": "stop"}
    parts: list[str] = []
    with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
        with client.stream("POST", url, json=body, headers={**headers, "Accept": "text/event-stream"}) as response:
            if response.status_code >= 400:
                response.read()
                raise classify_http_error(response.status_code, response.text, provider_name, response.headers.get("retry-after"))
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ProviderError(
                    f"{provider_name} answered without streaming", code=STREAM_UNSUPPORTED, retryable=False
                )
            for event in iter_sse_data(response):
                piece = read_event(event, state)
                if piece:
                    parts.append(piece)
                    yield piece
    # A stream can end three ways: finished, cut off at the token limit, or failed part-way.
    # Only the first is an answer; the text streamed so far must not be passed on as complete.
    usage = (int(state["input_tokens"]), int(state["output_tokens"]))
    if state.get("error"):
        logger.warning("%s stream failed part-way: %s", provider_name, str(state["error"])[:LOG_EXCERPT_CHARS])
        error = ProviderError(f"{provider_name} failed while streaming the reply", code="provider_stream_error", retryable=True)
        error.billed_tokens = usage if any(usage) else None
        raise error
    raise_if_truncated(str(state["finish_reason"]), provider_name, usage=usage)
    return "".join(parts), state


def read_chat_completions_event(event: dict[str, Any], state: StreamState) -> str | None:
    if event.get("error"):
        state["error"] = event["error"]
        return None
    usage = event.get("usage") or {}
    if usage:
        state["input_tokens"] = int(usage.get("prompt_tokens", 0))
        state["output_tokens"] = int(usage.get("completion_tokens", 0))
    pieces = []
    for choice in event.get("choices") or []:
        state["finish_reason"] = choice.get("finish_reason") or state["finish_reason"]
        pieces.append((choice.get("delta") or {}).get("content") or "")
    return "".join(pieces)


def read_responses_event(event: dict[str, Any], state: StreamState) -> str | None:
    if event.get("type") == "response.output_text.delta":
        return str(event.get("delta") or "")
    kind = event.get("type")
    if kind in ("response.completed", "response.incomplete", "response.failed"):
        response = event.get("response") or {}
        usage = response.get("usage") or {}
        state["input_tokens"] = int(usage.get("input_tokens", 0))
        state["output_tokens"] = int(usage.get("output_tokens", 0))
        if kind == "response.incomplete":
            reason = str((response.get("incomplete_details") or {}).get("reason") or "incomplete")
            if reason in _TRUNCATED_REASONS:
                state["finish_reason"] = reason
            else:
                state["error"] = f"incomplete: {reason}"
        elif kind == "response.failed":
            state["error"] = response.get("error") or "response failed"
    elif kind == "error":
        state["error"] = event.get("message") or event.get("error") or "stream error"
    return None


def anthropic_input_tokens(usage: dict[str, Any]) -> int:
    """Every input token of an Anthropic call.

    Anthropic reports `input_tokens` as the uncached part only; tokens read from or written to the prompt
    cache are counted separately. This adapter always caches the system prompt, so leaving them out would
    hide most of the input from the run's token budget.
    """
    return (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
    )


def read_anthropic_event(event: dict[str, Any], state: StreamState) -> str | None:
    kind = event.get("type")
    if kind == "message_start":
        state["input_tokens"] = anthropic_input_tokens((event.get("message") or {}).get("usage") or {})
    elif kind == "error":
        state["error"] = event.get("error") or "stream error"
    elif kind == "message_delta":
        state["output_tokens"] = int((event.get("usage") or {}).get("output_tokens", 0))
        state["finish_reason"] = (event.get("delta") or {}).get("stop_reason") or state["finish_reason"]
    elif kind == "content_block_delta" and (event.get("delta") or {}).get("type") == "text_delta":
        return str(event["delta"].get("text") or "")
    return None

