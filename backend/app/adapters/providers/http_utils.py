from email.utils import parsedate_to_datetime
import json
import logging
from typing import Any, Callable, Generator

import httpx

from app.domain.errors import ProviderError

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


def classify_http_error(status_code: int, body_text: str, provider_name: str) -> ProviderError:
    logger.warning("%s returned HTTP %s: %s", provider_name, status_code, body_text[:300])
    if status_code == 429 or status_code >= 500:
        return ProviderError(
            f"{provider_name} is temporarily unavailable (HTTP {status_code})",
            code="provider_transient_error",
            retryable=True,
        )
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
                raise classify_http_error(response.status_code, response.text, provider_name)
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ProviderError(
                    f"{provider_name} answered without streaming", code=STREAM_UNSUPPORTED, retryable=False
                )
            for event in iter_sse_data(response):
                piece = read_event(event, state)
                if piece:
                    parts.append(piece)
                    yield piece
    return "".join(parts), state


def read_chat_completions_event(event: dict[str, Any], state: StreamState) -> str | None:
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
    if event.get("type") == "response.completed":
        usage = (event.get("response") or {}).get("usage") or {}
        state["input_tokens"] = int(usage.get("input_tokens", 0))
        state["output_tokens"] = int(usage.get("output_tokens", 0))
    return None


def read_anthropic_event(event: dict[str, Any], state: StreamState) -> str | None:
    kind = event.get("type")
    if kind == "message_start":
        state["input_tokens"] = int(((event.get("message") or {}).get("usage") or {}).get("input_tokens", 0))
    elif kind == "message_delta":
        state["output_tokens"] = int((event.get("usage") or {}).get("output_tokens", 0))
        state["finish_reason"] = (event.get("delta") or {}).get("stop_reason") or state["finish_reason"]
    elif kind == "content_block_delta" and (event.get("delta") or {}).get("type") == "text_delta":
        return str(event["delta"].get("text") or "")
    return None

