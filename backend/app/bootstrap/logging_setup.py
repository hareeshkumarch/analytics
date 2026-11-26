import contextvars
import json
import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from opentelemetry import trace

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|pk|rk|xai|gsk|nvapi|sk-ant|sk-proj)[-_][A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)\b(bearer|token|api[_-]?key|password|secret)(\s*[=:]\s*|\s+)(['\"]?)[^\s'\",;&]{6,}"),
    re.compile(r"(?i)(://[^:/\s@]+:)[^@\s/]+(@)"),
)
_configured = False


def redact_secrets(text: str) -> str:
    text = _SECRET_PATTERNS[0].sub("[REDACTED]", text)
    text = _SECRET_PATTERNS[1].sub("[REDACTED]", text)
    text = _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]", text)
    return _SECRET_PATTERNS[3].sub(r"\1[REDACTED]\2", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def clean_request_id(raw: str | None) -> str | None:
    return raw if raw and _SAFE_REQUEST_ID.match(raw) else None


@contextmanager
def run_context(run_id: str) -> Iterator[None]:
    token = run_id_var.set(run_id)
    try:
        yield
    finally:
        run_id_var.reset(token)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.run_id = run_id_var.get()
        span_context = trace.get_current_span().get_span_context()
        record.trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else None
        return True


class JsonFormatter(RedactingFormatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "run_id", "trace_id"):
            value = getattr(record, key, None)
            if value:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return redact_secrets(json.dumps(entry, default=str))


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    if os.environ.get("LOG_FORMAT", "text").strip().lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s %(name)s [req=%(request_id)s run=%(run_id)s] %(message)s")
        )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
