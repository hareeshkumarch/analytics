import asyncio
import base64
import binascii
import contextlib
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterator
from zoneinfo import ZoneInfo


from app.api.schemas import (
    ConnectorExtractRequest,
)
from app.domain.errors import (
    ConnectorError,
    DomainError,
    LimitExceededError,
    NotFoundError,
    ValidationError,
)
from app.domain.limits import (
    DEFAULT_DATA_LIMITS,
    DEFAULT_GLOBAL_LIMITS,
)
from app.domain.models import ConnectorFamily, RunStatus

logger = logging.getLogger(__name__)

TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}
)
SSE_HEARTBEAT_SECONDS = 15.0
SSE_WORKER_POLL_SECONDS = 1.0
SSE_MAX_STREAM_SECONDS = 1800.0
RUN_PAGE_DEFAULT = 200
RUN_PAGE_MAX = 500
EXPORT_ARTIFACT_PREFIX = "exp_"
RUN_LIST_ANSWER_KEYS = ("answer_id", "summary_markdown", "charts", "outcome", "criteria", "route")




@contextlib.contextmanager
def _materialized_connector_config(
    family: ConnectorFamily, raw_config: dict[str, Any], upload_bytes: bytes | None = None
) -> Iterator[dict[str, Any]]:
    cfg = dict(raw_config)
    cfg.pop("file_path", None)
    cfg.pop("raw_bytes", None)
    cfg.pop("upload_id", None)
    cfg.pop("files", None)
    sample = cfg.pop("sample", None)
    if sample is not None:
        if family != ConnectorFamily.UPLOADED_FILE:
            raise ValidationError("Sample data is imported as uploaded files.")
        cfg["files"] = _sample_files(str(sample))
        cfg["format"] = "csv"
    if upload_bytes is not None:
        cfg["raw_bytes"] = upload_bytes
    tmp_dir: str | None = None
    if "raw_base64" in cfg:
        try:
            cfg["raw_bytes"] = base64.b64decode(str(cfg.pop("raw_base64")), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError("raw_base64 is not valid base64") from exc
    raw = cfg.get("raw_bytes")
    database_files = {
        ConnectorFamily.UPLOADED_SQLITE: ("SQLite", "upload.sqlite"),
        ConnectorFamily.UPLOADED_DUCKDB: ("DuckDB", "upload.duckdb"),
    }
    if family in database_files and isinstance(raw, (bytes, bytearray)):
        label, filename = database_files[family]
        if len(raw) > DEFAULT_DATA_LIMITS.max_uploaded_file_bytes:
            raise LimitExceededError(f"Uploaded {label} file exceeds the 40,000,000 bytes limit")
        tmp_dir = tempfile.mkdtemp(prefix="ledgerline_db_upload_")
        db_path = os.path.join(tmp_dir, filename)
        with open(db_path, "wb") as fh:
            fh.write(bytes(raw))
        cfg.pop("raw_bytes", None)
        cfg["file_path"] = db_path
    try:
        yield cfg
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


SAMPLE_DATASETS = {"inr_acceptance": "Sample shop payments (INR)", "saas_subscriptions": "Sample SaaS subscriptions (USD)"}


def _sample_dir(name: str) -> Path:
    root = os.getenv("SAMPLE_DATA_DIR") or str(Path(__file__).resolve().parents[3] / "fixtures")
    return Path(root) / name


def _sample_files(name: str) -> list[tuple[str, bytes]]:
    if name not in SAMPLE_DATASETS:
        raise ValidationError(f"Unknown sample dataset {name!r}.")
    files = [(p.stem, p.read_bytes()) for p in sorted(_sample_dir(name).glob("*.csv"))]
    if not files:
        raise NotFoundError("The sample data isn't installed on this server.")
    return files


_URL_CONFIG_KEYS = ("url",)
_DROPPED_CONFIG_KEYS = ("raw_base64", "raw_bytes", "file_path", "upload_id", "files")


def _without_url_secrets(value: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))


def _redacted_source_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Connector config safe to persist: no uploaded file bytes, no URL query strings.

    Uploaded files arrive base64-encoded in the config (up to ~53 MB) and pre-signed or
    token-bearing URLs carry credentials in the query string; neither belongs in the
    sources table.
    """
    safe: dict[str, Any] = {}
    for key, value in raw_config.items():
        if key in _DROPPED_CONFIG_KEYS:
            continue
        if key in _URL_CONFIG_KEYS and isinstance(value, str):
            safe[key] = _without_url_secrets(value)
            continue
        safe[key] = value
    return safe


_SECRET_FORM_KEY = re.compile(r"pass|secret|token|api_?key|private|credential", re.IGNORECASE)


def _reimport_state(payload: ConnectorExtractRequest) -> dict[str, Any]:
    values = {}
    if payload.form is not None:
        values = {
            key: _without_url_secrets(value) if isinstance(value, str) and "://" in value else value
            for key, value in payload.form.values.items()
            if key not in payload.credentials and not _SECRET_FORM_KEY.search(key)
        }
    return {
        "connector_id": payload.form.connector_id if payload.form is not None else None,
        "values": values,
        "selected_tables": list(payload.selected_tables),
    }


async def _call_connector(fn: Any, *args: Any) -> Any:
    try:
        return await asyncio.to_thread(fn, *args)
    except DomainError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Connector call failed: %s", exc.__class__.__name__)
        raise ConnectorError(f"{exc.__class__.__name__}: {exc}"[:400]) from exc


MAX_RUNS_WITH_EVIDENCE = 200



def workspace_daily_quota() -> tuple[int | None, Decimal | None]:
    runs = os.environ.get("WORKSPACE_DAILY_RUN_LIMIT", "").strip()
    cost = os.environ.get("WORKSPACE_DAILY_COST_LIMIT_USD", "").strip()
    return (
        int(runs) if runs else DEFAULT_GLOBAL_LIMITS.workspace_daily_runs,
        Decimal(cost) if cost else DEFAULT_GLOBAL_LIMITS.workspace_daily_cost_usd,
    )


def start_of_business_day(business_timezone: str) -> datetime:
    local = datetime.now(ZoneInfo(business_timezone))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
