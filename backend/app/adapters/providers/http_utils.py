from email.utils import parsedate_to_datetime
import json
from typing import Any

from app.domain.errors import ProviderError


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
    safe_msg = body_text[:300]
    if status_code == 429 or status_code >= 500:
        return ProviderError(
            f"{provider_name} transient HTTP {status_code}: {safe_msg}",
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
        f"{provider_name} request rejected ({status_code}): {safe_msg}",
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
