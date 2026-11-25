import asyncio
import base64
import binascii
import contextlib
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Iterator

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.persistence.crypto import CredentialCipher
from app.adapters.persistence.database import create_session_factory
from app.adapters.persistence.pg_repositories import (
    PgCredentialRepository,
    PgBudgetLedger,
    PgEventLog,
    PgEvidenceRepository,
    PgPinRepository,
    PgRoutingRepository,
    PgRunRepository,
    PgSnapshotRepository,
)
from app.domain.events import EventType, RunEvent
from app.api.schemas import (
    ApproveSemanticsRequest,
    ClarifyRunRequest,
    ConnectorExtractRequest,
    ConnectorProbeRequest,
    CreateExportRequest,
    CreatePinRequest,
    CreateRunRequest,
    SaveProviderKeyRequest,
    UpdateRoutingRequest,
)
from app.application.ports.connectors import ExtractionRequest
from app.application.ports.providers import ModelRequest
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.application.services.ingestion import table_artifact_id, table_key
from app.application.services.charts import chart_from_dict
from app.application.services.profiling import profile_arrow_table, propose_metrics_from_schema
from app.bootstrap.container import AppContainer, build_container
from app.application.services.routing_service import routing_policy_from_saved
from app.domain.errors import (
    BudgetExhaustedError,
    ClarificationRequired,
    ConnectorError,
    DomainError,
    LimitExceededError,
    NotFoundError,
    PersistenceUnavailableError,
    SecurityPolicyError,
    ValidationError,
    VersionConflictError,
    public_message,
)
from app.domain.limits import (
    DEEP_BUDGET_LIMITS,
    DEFAULT_DATA_LIMITS,
    DEFAULT_GLOBAL_LIMITS,
    QUICK_BUDGET_LIMITS,
)
from app.domain.models import Answer, ConnectorFamily, MetricDefinition, RunMode, RunStatus
from app.domain.providers import AgentRole, ProviderId, RoutingPolicy

ALLOWED_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "testserver", "api", "edge"})
TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}
)
SSE_HEARTBEAT_SECONDS = 15.0
SSE_WORKER_POLL_SECONDS = 1.0
RUN_PAGE_DEFAULT = 200
RUN_PAGE_MAX = 500
EXPORT_ARTIFACT_PREFIX = "exp_"
RUN_LIST_ANSWER_KEYS = ("answer_id", "summary_markdown", "charts")

logger = logging.getLogger(__name__)


def validate_loopback_bind(host: str) -> None:
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise SecurityPolicyError(
            f"Refusing to bind auth-free POC to non-loopback host {host!r}; only 127.0.0.1 is permitted."
        )


@contextlib.contextmanager
def _materialized_connector_config(
    family: ConnectorFamily, raw_config: dict[str, Any], upload_bytes: bytes | None = None
) -> Iterator[dict[str, Any]]:
    cfg = dict(raw_config)
    cfg.pop("file_path", None)
    cfg.pop("raw_bytes", None)
    cfg.pop("upload_id", None)
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


_URL_CONFIG_KEYS = ("url",)
_DROPPED_CONFIG_KEYS = ("raw_base64", "raw_bytes", "file_path", "upload_id")


def _redacted_source_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Connector config safe to persist: no uploaded file bytes, no URL query strings.

    Uploaded files arrive base64-encoded in the config (up to ~53 MB) and pre-signed or
    token-bearing URLs carry credentials in the query string; neither belongs in the
    sources table.
    """
    from urllib.parse import urlsplit, urlunsplit

    safe: dict[str, Any] = {}
    for key, value in raw_config.items():
        if key in _DROPPED_CONFIG_KEYS:
            continue
        if key in _URL_CONFIG_KEYS and isinstance(value, str):
            parts = urlsplit(value)
            safe[key] = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
            continue
        safe[key] = value
    return safe


async def _call_connector(fn: Any, *args: Any) -> Any:
    try:
        return await asyncio.to_thread(fn, *args)
    except DomainError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Connector call failed: %s", exc.__class__.__name__)
        raise ConnectorError(f"{exc.__class__.__name__}: {exc}"[:400]) from exc


MAX_RUNS_WITH_EVIDENCE = 200


def create_app(
    container: AppContainer | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Agentic Analytics Workspace API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.container = container or build_container()

    sf = session_factory or create_session_factory()
    app.state.session_factory = sf
    bound_engine = sf.kw.get("bind")
    bound_dialect = getattr(bound_engine, "dialect", None)
    app.state.database_dialect = getattr(bound_dialect, "name", None)
    if getattr(bound_dialect, "name", None) == "postgresql":
        durable_budget_ledger = PgBudgetLedger(sf)
        app.state.container.budget_ledger = durable_budget_ledger
        app.state.container.routing_service.set_budget_ledger(durable_budget_ledger)
    app.state.repo_runs = PgRunRepository(sf)
    app.state.repo_events = PgEventLog(sf)
    app.state.repo_evidence = PgEvidenceRepository(sf)
    app.state.repo_creds = PgCredentialRepository(sf)
    app.state.repo_routing = PgRoutingRepository(sf)
    app.state.repo_snapshots = PgSnapshotRepository(sf)
    app.state.repo_pins = PgPinRepository(sf)

    def _db_unavailable(action: str) -> PersistenceUnavailableError:
        return PersistenceUnavailableError(
            f"Could not {action} because the database is unavailable.",
            remediation="Check that PostgreSQL is running and DATABASE_URL is correct, then try again.",
        )

    app.state.runs = {}
    app.state.answers = {}
    app.state.events = {}
    app.state.evidence = {}
    app.state.event_queues = {}
    app.state.event_lock = threading.RLock()
    app.state.bg_tasks = {}
    app.state.active_run_semaphore = asyncio.Semaphore(DEFAULT_GLOBAL_LIMITS.max_active_root_runs)
    app.state.idempotency = {}
    app.state.snapshots = {}
    app.state.semantics = {}
    app.state.pins = {}
    app.state.encrypted_keys = {}
    app.state.undecryptable_keys = set()
    app.state.routing_policies = {}

    def _load_saved_credentials() -> None:
        c: AppContainer = app.state.container
        try:
            cipher = CredentialCipher()
        except Exception:
            return
        try:
            creds = app.state.repo_creds.list_active_credentials()
            for cred in creds:
                pid_str = cred["provider"]
                if pid_str in ProviderId._value2member_map_:
                    pid = ProviderId(pid_str)
                    try:
                        plain = cipher.decrypt(cred["encrypted_payload"])
                        c.routing_service._api_keys[pid] = plain
                        app.state.encrypted_keys[f"{cred['workspace_id']}:{pid_str}"] = cred["encrypted_payload"]
                    except Exception as exc:
                        app.state.undecryptable_keys.add(f"{cred['workspace_id']}:{pid_str}")
                        logger.warning("Could not decrypt key for %s: %s", pid_str, exc)
        except Exception:
            logger.exception("Error loading saved credentials from DB")

    _load_saved_credentials()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            o.strip()
            for o in os.environ.get(
                "CORS_ALLOW_ORIGINS",
                "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:8080,http://localhost:8080",
            ).split(",")
            if o.strip()
        ],
        allow_origin_regex=os.environ.get("CORS_ALLOW_ORIGIN_REGEX", r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def host_header_guard(request: Request, call_next: Any) -> Any:
        host_header = (request.headers.get("host") or "").split(":")[0].lower()
        if host_header and host_header not in ALLOWED_LOCAL_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"code": "forbidden_host", "message": "Non-local Host header rejected"},
            )
        return await call_next(request)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        return JSONResponse(status_code=exc.http_status, content=exc.to_envelope(req_id).to_dict())

    @app.exception_handler(RequestValidationError)
    async def pydantic_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        field_errs = {
            ".".join(str(loc) for loc in err.get("loc", [])): str(err.get("msg", "Invalid field"))
            for err in exc.errors()
        }
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Request payload failed strict schema validation",
                "request_id": req_id,
                "retryable": False,
                "field_errors": field_errs,
                "remediation": "Remove unknown fields and supply valid types.",
            },
        )

    def _store_evidence(run_id: str, item: dict[str, Any]) -> None:
        # Called from parallel specialist threads; the lock keeps the first record of a run from being lost.
        with app.state.event_lock:
            store = app.state.evidence
            if run_id not in store:
                store[run_id] = {}
                while len(store) > MAX_RUNS_WITH_EVIDENCE:
                    store.pop(next(iter(store)))
            store[run_id][item["evidence_id"]] = item
        try:
            run_rec = app.state.runs.get(run_id) or app.state.repo_runs.get_run(run_id)
            if run_rec:
                app.state.repo_evidence.save(run_id, run_rec["workspace_id"], item)
        except Exception:
            logger.exception("Failed to persist evidence for run %s", run_id)

    def _prior_tool_usage(run_id: str) -> tuple[int, int]:
        try:
            events = app.state.repo_events.list_after(run_id, 0)
        except SQLAlchemyError as exc:
            logger.exception("Could not restore tool usage for run %s", run_id)
            raise _db_unavailable("restore the run's tool budget") from exc
        tool_events = [
            event
            for event in events
            if event.type == EventType.TOOL_RESULT and isinstance(event.payload.get("tool"), str)
        ]
        return len(tool_events), sum(event.payload.get("tool") == "run_query_ir" for event in tool_events)

    def _load_task_history(run_id: str, task_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            events = app.state.repo_events.list_after(run_id, 0)
            evidence_records = app.state.repo_evidence.list_run(run_id)
        except Exception:
            logger.exception("Could not restore specialist history for run %s", run_id)
            return [], []
        trace = [
            {key: value for key, value in event.payload.items() if key != "task_id"}
            for event in events
            if event.type == EventType.TOOL_RESULT and event.payload.get("task_id") == task_id
        ]
        evidence = [item for item in evidence_records if item.get("task_id") == task_id]
        return trace, evidence

    def _get_query_engine() -> SnapshotQueryEngine:
        engine = getattr(app.state, "query_engine", None)
        if engine is None:
            engine = SnapshotQueryEngine(app.state.container.artifacts, SqlGlotQueryCompiler())
            app.state.query_engine = engine
        return engine

    def _get_workspace_snapshot(workspace_id: str, snapshot_id: str) -> dict[str, Any]:
        snap = app.state.snapshots.get(snapshot_id)
        if snap is None and hasattr(app.state, "repo_snapshots"):
            snap = app.state.repo_snapshots.get_snapshot(snapshot_id)
            if snap is not None:
                app.state.snapshots[snapshot_id] = snap
        if not snap or snap.get("workspace_id") != workspace_id:
            raise HTTPException(status_code=404, detail="Snapshot not found in this workspace")
        return snap

    def _publish_event(run_id: str, evt_dict: dict[str, Any], *, skip_if_cancelled: bool = False) -> bool:
        with app.state.event_lock:
            rec = app.state.runs.get(run_id)
            if rec is None and hasattr(app.state, "repo_runs"):
                rec = app.state.repo_runs.get_run(run_id)
                if rec:
                    app.state.runs[run_id] = rec
            if rec is not None:
                if skip_if_cancelled and rec["status"] == RunStatus.CANCELLED.value:
                    return False
                candidate_sequence = int(rec["last_event_sequence"]) + 1
            else:
                candidate_sequence = int(evt_dict.get("sequence", 0)) + 1
            evt_dict = {**evt_dict, "sequence": candidate_sequence}

            if hasattr(app.state, "repo_events"):
                try:
                    ev_type_str = evt_dict.get("type", "run.status")
                    ev_occ = evt_dict.get("occurred_at")
                    if isinstance(ev_occ, str):
                        try:
                            occ_dt = datetime.fromisoformat(ev_occ)
                        except Exception:
                            occ_dt = datetime.now(timezone.utc)
                    else:
                        occ_dt = datetime.now(timezone.utc)
                    ev_obj = RunEvent(
                        schema_version=1,
                        event_id=evt_dict.get("event_id") or f"evt_{uuid.uuid4().hex[:10]}",
                        sequence=int(evt_dict.get("sequence", 1)),
                        workspace_id=evt_dict.get("workspace_id") or (rec["workspace_id"] if rec else "ws1"),
                        run_id=run_id,
                        type=EventType(ev_type_str) if ev_type_str in EventType._value2member_map_ else EventType.RUN_STATUS,
                        occurred_at=occ_dt,
                        payload=evt_dict.get("payload", {}),
                    )
                    persisted = app.state.repo_events.append(
                        ev_obj,
                        only_if_not_cancelled=skip_if_cancelled,
                    )
                    if persisted is None:
                        return False
                    evt_dict = {**evt_dict, "sequence": persisted.sequence}
                except Exception:
                    logger.exception("Failed to persist event to DB")
            if rec is not None:
                rec["last_event_sequence"] = max(
                    int(rec.get("last_event_sequence") or 0), int(evt_dict["sequence"])
                )
            app.state.events.setdefault(run_id, []).append(evt_dict)

            for loop, q in app.state.event_queues.get(run_id, []):
                try:
                    loop.call_soon_threadsafe(q.put_nowait, evt_dict)
                except RuntimeError:
                    pass
        return True

    def _publish_api_event(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        rec = app.state.runs[run_id]
        _publish_event(
            run_id,
            {
                "schema_version": 1,
                "event_id": f"evt_{uuid.uuid4().hex[:10]}",
                "sequence": 0,
                "workspace_id": rec["workspace_id"],
                "run_id": run_id,
                "type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            },
        )

    def _broadcast_persisted_event(event: RunEvent) -> None:
        evt_dict = event.to_sse_dict()
        with app.state.event_lock:
            rec = app.state.runs.get(event.run_id)
            if rec is not None:
                rec["last_event_sequence"] = max(
                    int(rec.get("last_event_sequence") or 0), event.sequence
                )
            app.state.events.setdefault(event.run_id, []).append(evt_dict)
            for loop, queue in app.state.event_queues.get(event.run_id, []):
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, evt_dict)
                except RuntimeError:
                    pass

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        c: AppContainer = app.state.container
        return {
            "status": "ok",
            "connectors": [f.value for f in c.connectors.supported_families()],
            "providers": [p.value for p in c.providers.supported_providers()],
            "limits": DEFAULT_DATA_LIMITS.to_safe_dict(),
            "run_limits": {
                lim.mode: {
                    "total_model_attempts": lim.total_model_attempts,
                    "max_replans": lim.max_replans,
                    "active_seconds": lim.active_seconds,
                }
                for lim in (QUICK_BUDGET_LIMITS, DEEP_BUDGET_LIMITS)
            },
        }

    async def _upload_bytes(workspace_id: str, config: dict[str, Any]) -> bytes | None:
        upload_id = config.get("upload_id")
        if upload_id is None:
            return None
        return await asyncio.to_thread(app.state.container.uploads.read, workspace_id, str(upload_id))

    @app.post("/api/v1/workspaces/{workspace_id}/uploads", status_code=201)
    async def create_upload(workspace_id: str, request: Request) -> dict[str, Any]:
        limit = DEFAULT_DATA_LIMITS.max_uploaded_file_bytes
        too_large = LimitExceededError(f"Uploaded file exceeds the {limit:,} bytes limit")
        if int(request.headers.get("content-length") or 0) > limit:
            raise too_large
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit:
                raise too_large
        if not body:
            raise ValidationError("The uploaded file is empty")
        upload_id = await asyncio.to_thread(app.state.container.uploads.save, workspace_id, bytes(body))
        return {"upload_id": upload_id, "byte_size": len(body)}

    @app.post("/api/v1/workspaces/{workspace_id}/connectors/probe")
    async def probe_connector(workspace_id: str, payload: ConnectorProbeRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            upload = await _upload_bytes(workspace_id, payload.config)
            with _materialized_connector_config(payload.family, payload.config, upload) as cfg:
                res = await _call_connector(
                    c.connectors.get_discovery(payload.family).probe, cfg, payload.credentials
                )
        except DomainError as exc:
            return {
                "ok": False,
                "family": payload.family.value,
                "message": str(exc),
                "remediation": exc.remediation,
                "capabilities": {"transactional_snapshot": False, "resumable_cursor": False},
                "details": {},
            }
        return {
            "ok": res.ok,
            "family": res.family.value,
            "message": res.message,
            "capabilities": {
                "transactional_snapshot": res.capabilities.transactional_snapshot,
                "resumable_cursor": res.capabilities.resumable_cursor,
            },
            "details": res.details,
        }

    @app.post("/api/v1/workspaces/{workspace_id}/connectors/discover")
    async def discover_connector(workspace_id: str, payload: ConnectorProbeRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        upload = await _upload_bytes(workspace_id, payload.config)
        with _materialized_connector_config(payload.family, payload.config, upload) as cfg:
            tables = await _call_connector(
                c.connectors.get_discovery(payload.family).discover, cfg, payload.credentials
            )
        return {
            "tables": [
                {
                    "name": t.name,
                    "estimated_rows": t.estimated_rows,
                    "columns": [
                        {"name": col.name, "arrow_type": col.arrow_type, "is_leading_zero_id": col.is_leading_zero_id}
                        for col in t.columns
                    ],
                }
                for t in tables
            ]
        }

    @app.post("/api/v1/workspaces/{workspace_id}/connectors/extract", status_code=201)
    async def extract_connector(workspace_id: str, payload: ConnectorExtractRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        job_id = f"job_{uuid.uuid4().hex[:10]}"
        workspace_namespace = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:12]
        source_prefix = f"src_{workspace_namespace}_"
        source_config = _redacted_source_config(dict(payload.config))
        requested_source_id = payload.source_id or "src_" + hashlib.sha256(
            json.dumps([payload.family.value, source_config], sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
        source_id = (
            requested_source_id
            if requested_source_id.startswith(source_prefix)
            else f"{source_prefix}{requested_source_id.removeprefix('src_')[:42]}"
        )
        try:
            version = app.state.repo_snapshots.reserve_snapshot_version(
                workspace_id,
                source_id,
                payload.family.value,
                payload.selected_tables[0] if payload.selected_tables else "upload",
                source_config,
            )
        except SQLAlchemyError as exc:
            logger.exception("Failed to reserve a snapshot version")
            raise _db_unavailable("start the import") from exc
        req = ExtractionRequest(
            workspace_id=workspace_id,
            source_id=source_id,
            job_id=job_id,
            selected_tables=tuple(payload.selected_tables),
            selected_columns={k: tuple(v) for k, v in payload.selected_columns.items()},
            limits=DEFAULT_DATA_LIMITS,
            version=version,
        )
        upload = await _upload_bytes(workspace_id, payload.config)
        with _materialized_connector_config(payload.family, payload.config, upload) as cfg:
            batches, manifest = await _call_connector(
                c.connectors.get_extractor(payload.family).extract,
                cfg,
                payload.credentials,
                req,
                lambda: False,
            )
            batches = list(batches)
        content_hash = hashlib.sha256()
        for batch in sorted(batches, key=lambda b: b.table_name):
            content_hash.update(batch.table_name.encode("utf-8"))
            content_hash.update(batch.arrow_ipc_bytes)
        c.artifacts.reserve_workspace_bytes(workspace_id, manifest.total_normalized_bytes)
        try:
            for batch in batches:
                staged = c.artifacts.stage_bytes(
                    job_id, f"{table_key(batch.table_name)}.arrow", batch.arrow_ipc_bytes, "application/vnd.apache.arrow.stream"
                )
                c.artifacts.publish_staged(job_id, staged, table_artifact_id(manifest.artifact_id, batch.table_name))
        finally:
            c.artifacts.cleanup_job_staging(job_id)

        proposed_metrics: list[MetricDefinition] = []
        for tbl in manifest.tables:
            proposed_metrics.extend(propose_metrics_from_schema(tbl))

        snap_dict = {
            "snapshot_id": manifest.snapshot_id,
            "workspace_id": workspace_id,
            "source_id": source_id,
            "version": manifest.version,
            "total_rows": manifest.total_rows,
            "total_normalized_bytes": manifest.total_normalized_bytes,
            "is_sampled": manifest.is_sampled,
            "consistency_level": manifest.consistency_level.value,
            "sha256": content_hash.hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_family": payload.family.value,
            "schema_fingerprint": manifest.schema_fingerprint,
            "artifact_id": manifest.artifact_id,
            "table_artifacts": {
                b.table_name: table_artifact_id(manifest.artifact_id, b.table_name) for b in batches
            },
            "tables": [t.table_name for t in manifest.tables],
            "table_details": [
                {
                    "name": t.table_name,
                    "row_count": t.row_count,
                    "normalized_bytes": t.normalized_bytes,
                    "columns": [
                        {
                            "name": col.name,
                            "type": col.arrow_type,
                            "nullable": col.nullable,
                            "leading_zero_id": col.is_leading_zero_id,
                            "currency": col.currency_code,
                        }
                        for col in t.columns
                    ],
                }
                for t in manifest.tables
            ],
            "diagnostics": {
                "extraction_started_at": manifest.extraction_started_at.isoformat(),
                "extraction_completed_at": manifest.extraction_completed_at.isoformat(),
                "is_complete": manifest.is_complete,
                "is_sampled": manifest.is_sampled,
                "warnings": [],
                "source_summary": dict(manifest.source_config_summary),
            },
        }
        sem_id = f"sem_{uuid.uuid4().hex[:10]}"
        sem_dict = {
            "semantic_version_id": sem_id,
            "snapshot_id": manifest.snapshot_id,
            "version": 1,
            "approved": False,
            "metrics": [
                {
                    "metric_id": m.metric_id,
                    "display_name": m.display_name,
                    "expression": m.expression,
                    "grain": m.grain,
                    "unit": m.unit,
                    "currency": m.currency,
                }
                for m in proposed_metrics
            ],
        }
        try:
            app.state.repo_snapshots.save_snapshot(
                workspace_id=workspace_id,
                source_id=source_id,
                snapshot_id=manifest.snapshot_id,
                version=manifest.version,
                schema_fingerprint=manifest.schema_fingerprint,
                manifest_json=snap_dict,
                artifact_id=manifest.artifact_id,
                sha256=content_hash.hexdigest(),
                source_config=source_config,
                semantics_json=sem_dict,
            )
        except SQLAlchemyError as exc:
            logger.exception("Failed to persist snapshot to DB")
            raise _db_unavailable("save the imported dataset") from exc
        app.state.snapshots[manifest.snapshot_id] = snap_dict
        app.state.semantics[manifest.snapshot_id] = sem_dict
        if upload is not None:
            c.uploads.delete(workspace_id, str(payload.config["upload_id"]))

        return {
            "snapshot": snap_dict,
            "semantics": sem_dict,
        }

    @app.get("/api/v1/workspaces/{workspace_id}/snapshots")
    def list_snapshots(workspace_id: str) -> dict[str, Any]:
        if hasattr(app.state, "repo_snapshots"):
            db_snaps = app.state.repo_snapshots.list_snapshots(workspace_id)
            if db_snaps:
                return {"snapshots": db_snaps}
        items = []
        for snap in app.state.snapshots.values():
            if snap["workspace_id"] != workspace_id:
                continue
            sem = app.state.semantics.get(snap["snapshot_id"]) or {}
            items.append(
                {
                    **snap,
                    "semantic_version_id": sem.get("semantic_version_id"),
                    "approved": bool(sem.get("approved")),
                }
            )
        # Same contract as the database path: newest first.
        items.sort(key=lambda s: str(s.get("created_at") or ""), reverse=True)
        return {"snapshots": items}

    @app.get("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/profile")
    def profile_snapshot(workspace_id: str, snapshot_id: str, table: str) -> dict[str, Any]:
        snap = _get_workspace_snapshot(workspace_id, snapshot_id)
        arrow_table = _get_query_engine().load_table(snap, table)
        profiles = profile_arrow_table(arrow_table, is_sampled=bool(snap.get("is_sampled", False)))
        return {
            "snapshot_id": snapshot_id,
            "table": table,
            "columns": [
                {
                    "column_name": profile.column_name,
                    "null_count": profile.null_count,
                    "distinct_count": profile.distinct_count,
                    "duplicate_count": profile.duplicate_count,
                    "min_value": profile.min_value,
                    "max_value": profile.max_value,
                    "is_sampled": profile.is_sampled,
                }
                for profile in profiles
            ],
        }

    @app.get("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/preview")
    def preview_snapshot(
        workspace_id: str,
        snapshot_id: str,
        table: str,
        limit: int = Query(default=100, ge=1, le=DEFAULT_DATA_LIMITS.max_preview_rows),
    ) -> dict[str, Any]:
        snap = _get_workspace_snapshot(workspace_id, snapshot_id)
        preview = _get_query_engine().preview(snap, table, limit)
        return {"snapshot_id": snapshot_id, "table": table, "limit": limit, **preview}

    @app.get("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/diagnostics")
    def snapshot_diagnostics(workspace_id: str, snapshot_id: str) -> dict[str, Any]:
        snap = _get_workspace_snapshot(workspace_id, snapshot_id)
        diagnostics = snap.get("diagnostics")
        return {
            "snapshot_id": snapshot_id,
            "available": bool(diagnostics),
            "diagnostics": diagnostics,
            "consistency_level": snap.get("consistency_level"),
            "total_rows": snap.get("total_rows", 0),
            "total_normalized_bytes": snap.get("total_normalized_bytes", 0),
            "tables": [
                {
                    "name": table.get("name"),
                    "row_count": table.get("row_count", 0),
                    "normalized_bytes": table.get("normalized_bytes", 0),
                }
                for table in (snap.get("table_details") or [])
            ],
        }

    def _semantics_for(snapshot_id: str) -> dict[str, Any]:
        sem = app.state.semantics.get(snapshot_id)
        if sem is None and hasattr(app.state, "repo_snapshots"):
            sem = app.state.repo_snapshots.get_semantics(snapshot_id)
            if sem:
                # Cache so the orchestrator (which reads app.state) sees the definitions after a restart.
                app.state.semantics[snapshot_id] = sem
        return sem or {}

    def _resolve_binding(
        workspace_id: str, snapshot_id: str | None, semantic_version_id: str | None
    ) -> tuple[str, str]:
        if snapshot_id:
            snap = app.state.snapshots.get(snapshot_id)
            if snap is None and hasattr(app.state, "repo_snapshots"):
                snap = app.state.repo_snapshots.get_snapshot(snapshot_id)
                if snap:
                    app.state.snapshots[snapshot_id] = snap
            if not snap or snap["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="Snapshot not found in this workspace")
            sem = _semantics_for(snapshot_id)
            if sem and not sem.get("approved"):
                raise HTTPException(
                    status_code=409,
                    detail="This dataset's definitions are not approved yet. Review and approve them on the Data page first.",
                )
            # Always bind the snapshot's own semantic version; a client-sent id could belong to another snapshot.
            return snapshot_id, str(sem.get("semantic_version_id") or semantic_version_id or "")

        all_snaps = list(app.state.snapshots.values())
        if hasattr(app.state, "repo_snapshots"):
            db_snaps = app.state.repo_snapshots.list_snapshots(workspace_id)
            existing_ids = {s["snapshot_id"] for s in all_snaps}
            for d in db_snaps:
                if d["snapshot_id"] not in existing_ids:
                    all_snaps.append(d)
        owned = [s for s in all_snaps if s["workspace_id"] == workspace_id]
        approved = [
            s for s in owned if s.get("approved") or _semantics_for(s["snapshot_id"]).get("approved")
        ]
        if not approved:
            # Never fall back to an unapproved snapshot.
            return "", ""
        # Newest approved snapshot; list order differs between the database (newest first) and memory.
        chosen = max(approved, key=lambda s: str(s.get("created_at") or ""))
        app.state.snapshots.setdefault(chosen["snapshot_id"], chosen)
        sem_id = chosen.get("semantic_version_id") or str(_semantics_for(chosen["snapshot_id"]).get("semantic_version_id") or "")
        return chosen["snapshot_id"], sem_id

    @app.get("/api/v1/workspaces/{workspace_id}/semantics/{snapshot_id}")
    def get_semantics(workspace_id: str, snapshot_id: str) -> dict[str, Any]:
        if hasattr(app.state, "repo_snapshots"):
            sem = app.state.repo_snapshots.get_semantics(snapshot_id)
            if sem:
                return sem
        sem = app.state.semantics.get(snapshot_id)
        if not sem:
            raise HTTPException(status_code=404, detail="Semantic version not found")
        return sem

    @app.post("/api/v1/workspaces/{workspace_id}/semantics/{snapshot_id}/approve")
    def approve_semantics(workspace_id: str, snapshot_id: str, payload: ApproveSemanticsRequest) -> dict[str, Any]:
        try:
            res = app.state.repo_snapshots.approve_semantics(snapshot_id, payload.expected_version)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="Semantic version not found") from exc
        except VersionConflictError as exc:
            raise HTTPException(status_code=409, detail="Semantic version conflict") from exc
        except SQLAlchemyError as exc:
            logger.exception("Failed to approve semantics")
            raise _db_unavailable("approve the definitions") from exc
        app.state.semantics[snapshot_id] = res
        return res

    @app.get("/api/v1/workspaces/{workspace_id}/providers")
    def list_providers(workspace_id: str) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            db_keys = {cr["provider"] for cr in app.state.repo_creds.list_active_credentials(workspace_id)}
        except SQLAlchemyError as exc:
            logger.exception("Failed to load provider keys from DB")
            raise _db_unavailable("load saved provider keys") from exc
        def key_status(provider: ProviderId) -> str:
            if c.routing_service._api_keys.get(provider) or f"{workspace_id}:{provider.value}" in app.state.encrypted_keys:
                return "ready"
            if f"{workspace_id}:{provider.value}" in app.state.undecryptable_keys:
                return "needs_key"
            return "ready" if provider.value in db_keys else "missing"

        statuses = {p.provider_id: key_status(p.provider_id) for p in c.provider_profiles.values()}
        return {
            "workspace_id": workspace_id,
            "providers": [
                {
                    "provider_id": p.provider_id.value,
                    "api_profile": p.api_profile.value,
                    "endpoint_profile_id": p.endpoint_profile_id,
                    "configured": statuses[p.provider_id] == "ready",
                    "key_status": statuses[p.provider_id],
                }
                for p in c.provider_profiles.values()
            ],
            "models": [
                {
                    "model_id": m.model_id,
                    "provider_id": m.provider_id.value,
                    "roles": [r.value for r in m.roles],
                    "enabled": m.enabled,
                    "verification_date": m.verification_date,
                }
                for m in c.model_profiles.values()
            ],
        }

    @app.post("/api/v1/workspaces/{workspace_id}/providers/{provider_id}/test")
    async def test_provider_connection(workspace_id: str, provider_id: str) -> dict[str, Any]:
        from app.domain.providers import ProviderId
        c: AppContainer = app.state.container
        pid = ProviderId(provider_id)
        api_key = c.routing_service._api_keys.get(pid, "")
        if not api_key:
            return {
                "provider_id": pid.value,
                "status": "setup_required",
                "message": "No API key configured for this provider",
            }
        model = next((m for m in c.model_profiles.values() if m.provider_id == pid and m.enabled), None)
        if not model:
            return {"provider_id": pid.value, "status": "no_enabled_model"}

        t0 = time.monotonic()
        req = ModelRequest(
            request_id=f"probe_{uuid.uuid4().hex[:8]}",
            run_id="synthetic_probe",
            role=AgentRole.DATA_SPECIALIST,
            model_id=model.model_id,
            messages=({"role": "user", "content": "Reply with OK."},),
            max_output_tokens=16,
            timeout_seconds=10,
        )
        resp = await asyncio.to_thread(
            c.providers.get(pid).invoke, req, c.provider_profiles[pid], model, api_key
        )
        return {
            "provider_id": pid.value,
            "model_id": model.model_id,
            "status": "ok",
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "usage_complete": resp.input_tokens > 0 or resp.output_tokens > 0,
        }

    @app.get("/api/v1/workspaces/{workspace_id}/routing")
    def get_routing_policy(workspace_id: str) -> dict[str, Any]:
        current = _policy_for_workspace(workspace_id)
        return {
            "workspace_id": workspace_id,
            "policy_version": current.policy_version,
            "preset": current.preset.value,
            "fallback_enabled": current.fallback_enabled,
            "role_routes": {role.value: list(models) for role, models in current.role_routes.items()},
            "hard_cost_ceiling_usd": (
                str(current.hard_cost_ceiling_usd) if current.hard_cost_ceiling_usd is not None else None
            ),
        }

    @app.put("/api/v1/workspaces/{workspace_id}/routing")
    def update_routing_policy(workspace_id: str, payload: UpdateRoutingRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        current = _policy_for_workspace(workspace_id)

        if payload.expected_version != current.policy_version:
            raise HTTPException(status_code=409, detail="Routing policy version conflict")

        role_routes = dict(current.role_routes)
        if payload.role_routes is not None:
            for role_name, model_ids in payload.role_routes.items():
                try:
                    role = AgentRole(role_name)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=f"Unknown routing role: {role_name}") from exc
                normalized_ids = tuple(dict.fromkeys(model_id.strip() for model_id in model_ids if model_id.strip()))
                if not normalized_ids:
                    raise HTTPException(status_code=422, detail=f"At least one model is required for {role_name}")
                for model_id in normalized_ids:
                    model = c.model_profiles.get(model_id)
                    if model is None or not model.enabled or role not in model.roles:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Model {model_id!r} is not enabled for role {role_name}",
                        )
                role_routes[role] = normalized_ids

        hard_cost_ceiling = current.hard_cost_ceiling_usd
        if "hard_cost_ceiling_usd" in payload.model_fields_set:
            hard_cost_ceiling = payload.hard_cost_ceiling_usd
        preset = payload.preset or current.preset
        fallback_enabled = (
            payload.fallback_enabled if payload.fallback_enabled is not None else current.fallback_enabled
        )

        pol_dict = {
            "preset": preset.value,
            "fallback_enabled": fallback_enabled,
            "role_routes": {r.value: list(models) for r, models in role_routes.items()},
            "allowed_providers": [p.value for p in current.allowed_providers],
            "hard_cost_ceiling_usd": str(hard_cost_ceiling) if hard_cost_ceiling is not None else None,
        }
        try:
            res = app.state.repo_routing.save_policy(workspace_id, pol_dict, expected_version=payload.expected_version)
        except VersionConflictError as exc:
            raise HTTPException(status_code=409, detail="Routing policy version conflict") from exc
        except SQLAlchemyError as exc:
            logger.exception("Failed to save routing policy to DB")
            raise _db_unavailable("save the routing policy") from exc
        new_version = res["policy_version"]

        new_policy = RoutingPolicy(
            policy_version=new_version,
            preset=preset,
            fallback_enabled=fallback_enabled,
            role_routes=role_routes,
            allowed_providers=current.allowed_providers,
            hard_cost_ceiling_usd=hard_cost_ceiling,
        )
        app.state.routing_policies[workspace_id] = new_policy
        return {
            "workspace_id": workspace_id,
            "policy_version": new_policy.policy_version,
            "preset": new_policy.preset.value,
            "fallback_enabled": new_policy.fallback_enabled,
            "role_routes": {role.value: list(models) for role, models in new_policy.role_routes.items()},
            "hard_cost_ceiling_usd": (
                str(new_policy.hard_cost_ceiling_usd)
                if new_policy.hard_cost_ceiling_usd is not None
                else None
            ),
        }

    def _policy_for_workspace(workspace_id: str) -> RoutingPolicy:
        policy = app.state.routing_policies.get(workspace_id)
        if policy is not None:
            return policy
        try:
            saved = app.state.repo_routing.get_policy(workspace_id)
        except SQLAlchemyError as exc:
            logger.exception("Could not load routing policy for workspace %s", workspace_id)
            raise _db_unavailable("load the routing policy and cost limit") from exc
        policy = routing_policy_from_saved(saved, app.state.container.default_routing_policy)
        app.state.routing_policies[workspace_id] = policy
        return policy

    async def _execute_run_record(run_rec: dict[str, Any]) -> None:
        current_task = asyncio.current_task()
        run_id = str(run_rec["run_id"])
        workspace_id = str(run_rec["workspace_id"])
        snapshot_id = str(run_rec.get("snapshot_id") or "")
        semantic_version_id = str(run_rec.get("semantic_version_id") or "")

        def _is_cancelled() -> bool:
            return run_rec.get("status") == RunStatus.CANCELLED.value

        try:
            async with app.state.active_run_semaphore:
                if _is_cancelled():
                    return
                run_rec["status"] = RunStatus.RUNNING.value
                app.state.repo_runs.update_status(run_id, RunStatus.RUNNING)
                snapshot = app.state.snapshots.get(snapshot_id)
                if snapshot is None and snapshot_id:
                    snapshot = app.state.repo_snapshots.get_snapshot(snapshot_id)
                    if snapshot is not None:
                        app.state.snapshots[snapshot_id] = snapshot
                semantics = app.state.semantics.get(snapshot_id)
                if semantics is None and snapshot_id:
                    semantics = app.state.repo_snapshots.get_semantics(snapshot_id)
                    if semantics is not None:
                        app.state.semantics[snapshot_id] = semantics

                orch = InvestigationOrchestrator(app.state.container, query_engine=_get_query_engine())
                policy = _policy_for_workspace(workspace_id)
                final: dict[str, Any] = {}

                def _emit(evt: Any) -> None:
                    evt_dict = evt.to_sse_dict()
                    if _publish_event(run_id, evt_dict, skip_if_cancelled=True) and evt_dict["type"] == "run.finished":
                        final.update(evt_dict["payload"])

                prior_tool_invocations, prior_analytical_calls = _prior_tool_usage(run_id)
                orchestration_state = app.state.repo_runs.get_orchestration_state(run_id)
                prior_turns = app.state.repo_runs.conversation_turns(
                    workspace_id, str(run_rec.get("conversation_id") or ""), run_id
                )
                answer = await asyncio.to_thread(
                    orch.execute_local,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    question=str(run_rec.get("question") or ""),
                    mode=RunMode(str(run_rec.get("mode") or RunMode.QUICK.value)),
                    snapshot_id=snapshot_id,
                    semantic_version_id=semantic_version_id,
                    snapshot=snapshot,
                    semantics=semantics,
                    policy_override=policy,
                    emit_event=_emit,
                    should_cancel=_is_cancelled,
                    on_evidence=lambda ev: _store_evidence(run_id, ev),
                    initial_tool_invocations=prior_tool_invocations,
                    initial_analytical_calls=prior_analytical_calls,
                    evidence_lookup=lambda evidence_id: app.state.repo_evidence.get(run_id, evidence_id),
                    task_history_loader=lambda task_id: _load_task_history(run_id, task_id),
                    clarification_answer=orchestration_state.get("clarification_answer"),
                    prior_turns=prior_turns,
                    initial_plan=orchestration_state.get("plan"),
                    on_plan=lambda plan: app.state.repo_runs.save_plan(run_id, plan),
                )
                if _is_cancelled():
                    return
                app.state.answers[run_id] = answer
                final_status = str(final.get("status") or RunStatus.COMPLETED.value)
                run_rec["status"] = final_status
                run_rec["outcome"] = "answered" if final_status == RunStatus.COMPLETED.value else final_status
                run_rec["answer_id"] = answer.answer_id
                if final.get("error"):
                    run_rec["error"] = final["error"]
                app.state.repo_runs.save_answer(answer, final_status=final_status)
        except ClarificationRequired as exc:
            if not _is_cancelled():
                pending_events = app.state.repo_runs.pause_for_clarification(run_id, exc.prompt)
                latest = app.state.repo_runs.get_run(run_id)
                if latest is not None:
                    run_rec.update(latest)
                    app.state.runs[run_id] = run_rec
                for event in pending_events:
                    _broadcast_persisted_event(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Run %s failed", run_id)
            if not _is_cancelled():
                run_rec["status"] = RunStatus.FAILED.value
                run_rec["outcome"] = "failed"
                run_rec["error"] = public_message(exc)
                try:
                    app.state.repo_runs.update_status(
                        run_id, RunStatus.FAILED, outcome="failed", error=run_rec["error"]
                    )
                except SQLAlchemyError:
                    logger.exception("Could not record failure of run %s", run_id)
                _publish_api_event(
                    run_id, "run.finished", {"status": RunStatus.FAILED.value, "error": run_rec["error"]}
                )
        finally:
            if app.state.bg_tasks.get(run_id) is current_task:
                app.state.bg_tasks.pop(run_id, None)

    @app.on_event("startup")
    async def _resume_active_runs() -> None:
        if os.environ.get("RUN_EXECUTION_MODE", "inline").strip().lower() == "celery":
            return
        try:
            active_runs = app.state.repo_runs.list_active_runs()
        except Exception:
            logger.exception("Could not load active runs for resume")
            return
        for run_rec in active_runs:
            run_id = str(run_rec["run_id"])
            app.state.runs[run_id] = run_rec
            app.state.bg_tasks[run_id] = asyncio.create_task(_execute_run_record(run_rec))

    @app.post("/api/v1/workspaces/{workspace_id}/runs", status_code=202)
    async def create_run(
        workspace_id: str,
        payload: CreateRunRequest,
        idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        canonical_dict = payload.model_dump(mode="json")
        payload_hash = hashlib.sha256(json.dumps(canonical_dict, sort_keys=True).encode("utf-8")).hexdigest()
        if idempotency_key and idempotency_key in app.state.idempotency:
            prev_hash, prev_code, prev_resp = app.state.idempotency[idempotency_key]
            if prev_hash != payload_hash:
                raise HTTPException(status_code=409, detail="Idempotency-Key reused with different payload")
            return JSONResponse(status_code=prev_code, content=prev_resp)

        execution_mode = os.environ.get("RUN_EXECUTION_MODE", "inline").strip().lower()
        if execution_mode == "celery" and app.state.database_dialect != "postgresql":
            raise HTTPException(
                status_code=503,
                detail="Celery run execution requires PostgreSQL-backed persistence.",
            )
        try:
            queued_count = await asyncio.to_thread(app.state.repo_runs.count_queued_runs)
        except SQLAlchemyError as exc:
            logger.exception("Could not count queued runs in DB")
            raise _db_unavailable("check the run queue") from exc
        if queued_count >= DEFAULT_GLOBAL_LIMITS.max_queued_runs:
            raise BudgetExhaustedError(
                f"Global queue limit ({DEFAULT_GLOBAL_LIMITS.max_queued_runs} queued runs) reached."
            )

        parent = None
        if payload.follow_up_of:
            parent = await asyncio.to_thread(app.state.repo_runs.get_run, payload.follow_up_of)
            if not parent or parent["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="The run to follow up on was not found")
        snapshot_id, semantic_version_id = await asyncio.to_thread(
            _resolve_binding,
            workspace_id,
            payload.snapshot_id or (parent["snapshot_id"] if parent else None),
            payload.semantic_version_id,
        )
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        conv_id = str(parent["conversation_id"]) if parent else f"conv_{run_id.removeprefix('run_')}"
        try:
            run_rec = await asyncio.to_thread(
                app.state.repo_runs.create_run,
                run_id=run_id,
                workspace_id=workspace_id,
                conversation_id=conv_id,
                mode=payload.mode.value,
                status=RunStatus.QUEUED.value,
                question=payload.question,
                snapshot_id=snapshot_id or None,
                semantic_version_id=semantic_version_id or None,
            )
        except SQLAlchemyError as exc:
            logger.exception("Failed to create run in DB")
            raise _db_unavailable("start the run") from exc
        run_rec["clarification_revision"] = 0
        app.state.runs[run_id] = run_rec
        app.state.events[run_id] = []
        _publish_event(
            run_id,
            {
                "schema_version": 1,
                "event_id": f"evt_{uuid.uuid4().hex[:10]}",
                "sequence": 1,
                "workspace_id": workspace_id,
                "run_id": run_id,
                "type": "run.status",
                "occurred_at": now,
                "payload": {"status": RunStatus.QUEUED.value},
            },
        )

        async def _execute_bounded_run() -> None:
            await _execute_run_record(run_rec)

        if payload.execute_inline:
            await _execute_bounded_run()
        elif payload.background_async:
            if execution_mode == "celery":
                try:
                    from app.jobs.tasks import run_investigation_task

                    run_investigation_task.delay(
                        run_id,
                        workspace_id,
                        payload.question,
                        payload.mode.value,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Could not enqueue run %s", run_id)
                    run_rec["status"] = RunStatus.FAILED.value
                    run_rec["outcome"] = "failed"
                    run_rec["error"] = f"Worker queue unavailable: {exc}"[:500]
                    app.state.repo_runs.update_status(
                        run_id,
                        RunStatus.FAILED,
                        outcome="failed",
                        error=run_rec["error"],
                    )
                    _publish_api_event(
                        run_id,
                        "run.finished",
                        {"status": RunStatus.FAILED.value, "error": run_rec["error"]},
                    )
                    raise HTTPException(status_code=503, detail="Investigation worker queue is unavailable") from exc
            else:
                app.state.bg_tasks[run_id] = asyncio.create_task(_execute_bounded_run())

        resp_body = {
            "run_id": run_id,
            "status": run_rec["status"],
            "status_url": f"/api/v1/workspaces/{workspace_id}/runs/{run_id}",
            "events_url": f"/api/v1/workspaces/{workspace_id}/runs/{run_id}/events",
        }
        if idempotency_key:
            app.state.idempotency[idempotency_key] = (payload_hash, 202, resp_body)
        return JSONResponse(status_code=202, content=resp_body)

    @app.get("/api/v1/workspaces/{workspace_id}/runs")
    def list_runs(
        workspace_id: str,
        limit: int = Query(RUN_PAGE_DEFAULT, ge=1, le=RUN_PAGE_MAX),
        before: datetime | None = None,
    ) -> dict[str, Any]:
        try:
            runs = app.state.repo_runs.list_runs(workspace_id, limit=limit + 1, before=before)
            page = [
                {**run, "answer": {k: run["answer"][k] for k in RUN_LIST_ANSWER_KEYS if k in run["answer"]}}
                for run in runs[:limit]
            ]
            return {"runs": page, "next_before": page[-1]["created_at"] if len(runs) > limit else None}
        except SQLAlchemyError as exc:
            logger.exception("Failed to query runs from DB")
            raise _db_unavailable("load the run history") from exc

    @app.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}")
    def get_run(workspace_id: str, run_id: str) -> dict[str, Any]:
        run_rec = app.state.repo_runs.get_run(run_id)
        if run_rec:
            app.state.runs[run_id] = run_rec
        else:
            run_rec = app.state.runs.get(run_id)
        if not run_rec or run_rec["workspace_id"] != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found")
        return run_rec

    @app.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}/evidence")
    def list_run_evidence(workspace_id: str, run_id: str) -> dict[str, Any]:
        run_rec = app.state.runs.get(run_id)
        if not run_rec and hasattr(app.state, "repo_runs"):
            run_rec = app.state.repo_runs.get_run(run_id)
        if not run_rec or run_rec["workspace_id"] != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found")
        items = app.state.repo_evidence.list_run(run_id)
        if not items:
            items = list(app.state.evidence.get(run_id, {}).values())
        return {
            "run_id": run_id,
            "items": [{k: v for k, v in e.items() if k != "rows"} for e in items],
        }

    @app.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}/evidence/{evidence_id}")
    def get_run_evidence(workspace_id: str, run_id: str, evidence_id: str) -> dict[str, Any]:
        run_rec = app.state.runs.get(run_id)
        if not run_rec and hasattr(app.state, "repo_runs"):
            run_rec = app.state.repo_runs.get_run(run_id)
        if not run_rec or run_rec["workspace_id"] != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found")
        item = app.state.repo_evidence.get(run_id, evidence_id)
        if item is None:
            item = app.state.evidence.get(run_id, {}).get(evidence_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Evidence not found")
        return item

    @app.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}/events")
    async def stream_run_events(
        workspace_id: str,
        run_id: str,
        last_event_id: str | None = Header(default=None),
        after: int = 0,
    ) -> StreamingResponse:
        run_rec = await asyncio.to_thread(app.state.repo_runs.get_run, run_id)
        if run_rec:
            app.state.runs[run_id] = run_rec
        else:
            run_rec = app.state.runs.get(run_id)
        if not run_rec or run_rec["workspace_id"] != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found")
        cursor = int(last_event_id) if (last_event_id and last_event_id.isdigit()) else after

        def _frame(evt: dict[str, Any]) -> str:
            return f"id: {evt['sequence']}\nevent: {evt['type']}\ndata: {json.dumps(evt)}\n\n"

        def _event_dict(event: RunEvent) -> dict[str, Any]:
            return {
                "schema_version": event.schema_version,
                "event_id": event.event_id,
                "sequence": event.sequence,
                "workspace_id": event.workspace_id,
                "run_id": event.run_id,
                "type": event.type.value if hasattr(event.type, "value") else str(event.type),
                "occurred_at": event.occurred_at.isoformat()
                if hasattr(event.occurred_at, "isoformat")
                else str(event.occurred_at),
                "payload": event.payload,
            }

        async def event_gen() -> Any:
            live = run_rec.get("status") not in TERMINAL_RUN_STATUSES
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            sub = (loop, queue)
            if live:
                with app.state.event_lock:
                    app.state.event_queues.setdefault(run_id, []).append(sub)
            try:
                yield ": heartbeat\n\n"
                last_seq = cursor
                finished = False
                past_events = []
                if hasattr(app.state, "repo_events"):
                    try:
                        db_evts = await asyncio.to_thread(app.state.repo_events.list_after, run_id, cursor)
                        past_events = [_event_dict(event) for event in db_evts]
                    except Exception:
                        logger.exception("Failed to query past events from DB")
                if not past_events:
                    past_events = [e for e in app.state.events.get(run_id, []) if e["sequence"] > cursor]

                for evt in past_events:
                    if evt["sequence"] > last_seq:
                        last_seq = evt["sequence"]
                        finished = finished or evt["type"] == "run.finished"
                        yield _frame(evt)
                last_sent = time.monotonic()
                while live and not finished:
                    worker_run = run_id not in app.state.bg_tasks
                    try:
                        evt = await asyncio.wait_for(
                            queue.get(), timeout=SSE_WORKER_POLL_SECONDS if worker_run else SSE_HEARTBEAT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        if worker_run:
                            try:
                                new_events = await asyncio.to_thread(app.state.repo_events.list_after, run_id, last_seq)
                                for event in new_events:
                                    evt = _event_dict(event)
                                    last_seq = evt["sequence"]
                                    finished = evt["type"] == "run.finished"
                                    last_sent = time.monotonic()
                                    yield _frame(evt)
                                latest = await asyncio.to_thread(app.state.repo_runs.get_run, run_id)
                                if latest:
                                    app.state.runs[run_id] = latest
                                    if latest["status"] in TERMINAL_RUN_STATUSES and not new_events:
                                        break
                            except Exception:
                                logger.exception("Failed to poll worker events for run %s", run_id)
                            if finished:
                                break
                        if time.monotonic() - last_sent >= SSE_HEARTBEAT_SECONDS:
                            last_sent = time.monotonic()
                            yield ": heartbeat\n\n"
                        continue
                    if evt["sequence"] <= last_seq:
                        continue
                    last_seq = evt["sequence"]
                    finished = evt["type"] == "run.finished"
                    last_sent = time.monotonic()
                    yield _frame(evt)
            finally:
                if live:
                    with app.state.event_lock:
                        subs = app.state.event_queues.get(run_id, [])
                        if sub in subs:
                            subs.remove(sub)
                        if not subs:
                            app.state.event_queues.pop(run_id, None)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/workspaces/{workspace_id}/runs/{run_id}/clarify")
    async def clarify_run(workspace_id: str, run_id: str, payload: ClarifyRunRequest) -> dict[str, Any]:
        run_rec = await asyncio.to_thread(app.state.repo_runs.get_run, run_id) or app.state.runs.get(run_id)
        if run_rec:
            app.state.runs[run_id] = run_rec
        if not run_rec or run_rec["workspace_id"] != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found")
        if run_rec.get("status") != RunStatus.AWAITING_INPUT.value:
            raise HTTPException(status_code=409, detail="Run is not awaiting clarification")
        answer = payload.answer.strip()
        if not answer:
            raise HTTPException(status_code=422, detail="Clarification answer cannot be blank")
        try:
            queued_count = await asyncio.to_thread(app.state.repo_runs.count_queued_runs)
        except SQLAlchemyError as exc:
            logger.exception("Could not count queued runs before clarification resume")
            raise _db_unavailable("check the run queue") from exc
        if queued_count >= DEFAULT_GLOBAL_LIMITS.max_queued_runs:
            raise BudgetExhaustedError(
                f"Global queue limit ({DEFAULT_GLOBAL_LIMITS.max_queued_runs} queued runs) reached."
            )
        updated, event = await asyncio.to_thread(
            app.state.repo_runs.submit_clarification,
            run_id,
            expected_revision=payload.revision,
            answer=answer,
        )
        if updated is None or event is None:
            raise HTTPException(status_code=409, detail="Clarification revision conflict")
        run_rec.update(updated)
        app.state.runs[run_id] = run_rec
        _broadcast_persisted_event(event)

        execution_mode = os.environ.get("RUN_EXECUTION_MODE", "inline").strip().lower()
        if execution_mode == "celery":
            try:
                from app.jobs.tasks import run_investigation_task

                run_investigation_task.delay(
                    run_id,
                    workspace_id,
                    str(run_rec.get("question") or ""),
                    str(run_rec.get("mode") or RunMode.QUICK.value),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Could not resume run %s", run_id)
                run_rec["status"] = RunStatus.FAILED.value
                run_rec["outcome"] = "failed"
                run_rec["error"] = f"Worker queue unavailable: {exc}"[:500]
                await asyncio.to_thread(
                    app.state.repo_runs.update_status,
                    run_id,
                    RunStatus.FAILED,
                    outcome="failed",
                    error=run_rec["error"],
                )
                _publish_api_event(
                    run_id,
                    "run.finished",
                    {"status": RunStatus.FAILED.value, "error": run_rec["error"]},
                )
                raise HTTPException(status_code=503, detail="Could not resume the run") from exc
        else:
            app.state.bg_tasks[run_id] = asyncio.create_task(_execute_run_record(run_rec))
        return {
            "run_id": run_id,
            "status": run_rec["status"],
            "revision": run_rec["clarification_revision"],
        }

    @app.post("/api/v1/workspaces/{workspace_id}/runs/{run_id}/cancel")
    async def cancel_run(workspace_id: str, run_id: str) -> dict[str, Any]:
        run_rec = await asyncio.to_thread(app.state.repo_runs.get_run, run_id)
        if run_rec:
            app.state.runs[run_id] = run_rec
        else:
            run_rec = app.state.runs.get(run_id)
        if not run_rec or run_rec["workspace_id"] != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found")
        with app.state.event_lock:
            events = app.state.events.get(run_id, [])
            if events and events[-1]["type"] == "run.finished":
                return {"run_id": run_id, "status": str(events[-1]["payload"].get("status", run_rec["status"]))}
            latest = app.state.repo_runs.get_run(run_id)
            if latest:
                run_rec = latest
                app.state.runs[run_id] = latest
                if run_rec["status"] in TERMINAL_RUN_STATUSES:
                    return {"run_id": run_id, "status": run_rec["status"]}
            if run_rec["status"] not in TERMINAL_RUN_STATUSES:
                was_running = run_rec["status"] == RunStatus.RUNNING.value
                run_rec["status"] = RunStatus.CANCELLED.value
                run_rec["outcome"] = "cancelled"
                app.state.repo_runs.update_status(run_id, RunStatus.CANCELLED, outcome="cancelled")
                bg = app.state.bg_tasks.get(run_id)
                if bg and not bg.done() and not was_running:
                    bg.cancel()
                _publish_api_event(run_id, "run.finished", {"status": RunStatus.CANCELLED.value})
        return {"run_id": run_id, "status": run_rec["status"]}

    @app.delete("/api/v1/workspaces/{workspace_id}/runs/{run_id}")
    def delete_run(workspace_id: str, run_id: str) -> dict[str, Any]:
        try:
            run_rec = app.state.repo_runs.get_run(run_id)
            if not run_rec or run_rec["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="Run not found")
            if run_rec["status"] not in TERMINAL_RUN_STATUSES:
                raise HTTPException(status_code=409, detail="Cancel the run before deleting it.")
            app.state.repo_runs.delete_run(workspace_id, run_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to delete run %s", run_id)
            raise _db_unavailable("delete the run") from exc
        for store in (app.state.runs, app.state.answers, app.state.events, app.state.evidence):
            store.pop(run_id, None)
        app.state.pins[workspace_id] = [p for p in app.state.pins.get(workspace_id, []) if p.get("run_id") != run_id]
        return {"run_id": run_id, "deleted": True}

    @app.delete("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}")
    def delete_snapshot(workspace_id: str, snapshot_id: str, force: bool = False) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            existing = app.state.repo_snapshots.get_snapshot(snapshot_id)
            if not existing or existing["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="Dataset not found")
            pinned = app.state.repo_snapshots.pinned_runs_for_snapshot(workspace_id, snapshot_id)
            if pinned and not force:
                raise HTTPException(
                    status_code=409,
                    detail=f"{len(pinned)} pinned answer(s) use this dataset; delete with force=true to remove it anyway.",
                )
            removed = app.state.repo_snapshots.delete_snapshot(workspace_id, snapshot_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to delete snapshot %s", snapshot_id)
            raise _db_unavailable("delete the dataset") from exc
        if removed is None:
            raise HTTPException(status_code=404, detail="Dataset not found")
        for artifact_id in (removed.get("table_artifacts") or {}).values():
            c.artifacts.delete(str(artifact_id))
        c.artifacts.release_workspace_bytes(workspace_id, int(removed.get("total_normalized_bytes") or 0))
        app.state.snapshots.pop(snapshot_id, None)
        app.state.semantics.pop(snapshot_id, None)
        return {"snapshot_id": snapshot_id, "deleted": True}

    @app.get("/api/v1/workspaces/{workspace_id}/pins")
    def list_pins(workspace_id: str) -> dict[str, Any]:
        try:
            return {"pins": app.state.repo_pins.list_pins(workspace_id)}
        except SQLAlchemyError as exc:
            logger.exception("Failed to query pins from DB")
            raise _db_unavailable("load pinned answers") from exc

    @app.post("/api/v1/workspaces/{workspace_id}/pins", status_code=201)
    def create_pin(workspace_id: str, payload: CreatePinRequest) -> dict[str, Any]:
        run_rec = app.state.repo_runs.get_run(payload.run_id)
        if run_rec:
            app.state.runs[payload.run_id] = run_rec
        else:
            run_rec = app.state.runs.get(payload.run_id)
        if not run_rec or run_rec.get("workspace_id") != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found in this workspace")
        if run_rec.get("status") != RunStatus.COMPLETED.value:
            raise HTTPException(status_code=409, detail="Only completed runs can be pinned")
        try:
            existing_pins = app.state.repo_pins.list_pins(workspace_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to check existing pins in DB")
            raise _db_unavailable("check existing pins") from exc
        existing = next((pin for pin in existing_pins if pin.get("run_id") == payload.run_id), None)
        if existing:
            return existing
        pin_id = f"pin_{uuid.uuid4().hex[:10]}"
        try:
            res = app.state.repo_pins.create_pin(
                pin_id=pin_id,
                workspace_id=workspace_id,
                title=payload.title,
                question_text=payload.question_text,
                run_id=payload.run_id,
            )
        except SQLAlchemyError as exc:
            logger.exception("Failed to create pin in DB")
            raise _db_unavailable("pin the answer") from exc
        app.state.pins.setdefault(workspace_id, []).append(res)
        return res

    @app.delete("/api/v1/workspaces/{workspace_id}/pins/{pin_id}")
    def delete_pin(workspace_id: str, pin_id: str) -> dict[str, Any]:
        removed = False
        try:
            removed = app.state.repo_pins.delete_pin(workspace_id, pin_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to delete pin in DB")
            raise _db_unavailable("remove the pin") from exc
        mem = app.state.pins.get(workspace_id, [])
        kept = [p for p in mem if p.get("pin_id") != pin_id]
        removed = removed or len(kept) != len(mem)
        app.state.pins[workspace_id] = kept
        if not removed:
            raise HTTPException(status_code=404, detail="Pin not found")
        return {"pin_id": pin_id, "deleted": True}

    @app.post("/api/v1/workspaces/{workspace_id}/pins/{pin_id}/refresh", status_code=202)
    async def refresh_pin(workspace_id: str, pin_id: str) -> dict[str, Any]:
        pins = app.state.pins.get(workspace_id, [])
        pin = next((p for p in pins if p["pin_id"] == pin_id), None)
        if not pin and hasattr(app.state, "repo_pins"):
            for p in app.state.repo_pins.list_pins(workspace_id):
                if p["pin_id"] == pin_id:
                    pin = p
                    break
        if not pin:
            raise HTTPException(status_code=404, detail="Pin not found")
        original_run = app.state.runs.get(pin["run_id"])
        if original_run is None and hasattr(app.state, "repo_runs"):
            original_run = app.state.repo_runs.get_run(pin["run_id"])
        try:
            mode = RunMode(str((original_run or {}).get("mode", RunMode.QUICK.value)))
        except ValueError:
            mode = RunMode.QUICK
        refreshed = await create_run(
            workspace_id,
            CreateRunRequest(question=pin["question_text"], mode=mode, background_async=True),
            # Called directly, so the Header() default object would otherwise act as a shared idempotency key.
            idempotency_key=None,
        )
        run_data = json.loads(refreshed.body)
        return {"pin_id": pin_id, "refresh_run_id": run_data["run_id"], "status": run_data["status"]}

    @app.post("/api/v1/workspaces/{workspace_id}/exports", status_code=201)
    async def create_export(workspace_id: str, payload: CreateExportRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        fmt = payload.format.lower()
        run_id = payload.run_id
        ans = app.state.answers.get(run_id)
        if ans is None and hasattr(app.state, "repo_runs"):
            run_rec = app.state.repo_runs.get_run(run_id)
            if run_rec and run_rec.get("answer"):
                ans_data = run_rec["answer"]
                from app.domain.models import Claim
                ans = Answer(
                    answer_id=ans_data.get("answer_id") or f"ans_{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    summary_markdown=ans_data.get("summary_markdown", ""),
                    claims=tuple(
                        Claim(
                            claim_id=cl["claim_id"],
                            label=cl["label"],
                            statement=cl["statement"],
                            value=Decimal(str(cl["value"])) if cl.get("value") is not None else None,
                            unit=cl.get("unit"),
                            currency=cl.get("currency"),
                            evidence_ids=tuple(cl.get("evidence_ids", ())),
                        )
                        for cl in ans_data.get("claims", [])
                    ),
                    charts=tuple(chart_from_dict(ch) for ch in ans_data.get("charts") or ()),
                    limitations=tuple(ans_data.get("limitations", ())),
                    snapshot_id=ans_data.get("snapshot_id"),
                    semantic_version_id=ans_data.get("semantic_version_id"),
                    created_at=datetime.now(timezone.utc),
                )
        if ans is None:
            if not payload.summary_markdown.strip():
                raise HTTPException(status_code=404, detail="No answer found for this run and no content supplied")
            ans = Answer(
                answer_id=f"ans_{uuid.uuid4().hex[:8]}",
                run_id=run_id,
                summary_markdown=payload.summary_markdown,
                claims=(),
                charts=(),
                limitations=(),
                snapshot_id=payload.snapshot_id,
                semantic_version_id=payload.semantic_version_id,
                created_at=datetime.now(timezone.utc),
            )
        if fmt == "csv":
            raw = await asyncio.to_thread(
                c.export_service.export_csv,
                ("claim_id", "statement"),
                tuple((cl.claim_id, cl.statement) for cl in ans.claims),
            )
            ctype = "text/csv"
        elif fmt == "pdf":
            raw = await asyncio.to_thread(c.export_service.export_pdf, ans)
            ctype = "application/pdf"
        elif fmt == "docx":
            raw = await asyncio.to_thread(c.export_service.export_docx, ans)
            ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            raw = await asyncio.to_thread(c.export_service.export_html, ans)
            ctype = "text/html"

        art_id = f"{EXPORT_ARTIFACT_PREFIX}{uuid.uuid4().hex[:10]}"
        staged = c.artifacts.stage_bytes(art_id, f"export.{fmt}", raw, ctype)
        stored = c.artifacts.publish_staged(art_id, staged, art_id)
        c.artifacts.cleanup_job_staging(art_id)
        return {
            "artifact_id": stored.artifact_id,
            "sha256": stored.sha256,
            "byte_size": stored.byte_size,
            "format": fmt,
            "download_url": f"/api/v1/workspaces/{workspace_id}/exports/{stored.artifact_id}",
        }

    @app.get("/api/v1/workspaces/{workspace_id}/exports/{artifact_id}")
    def download_export(workspace_id: str, artifact_id: str) -> Response:
        c: AppContainer = app.state.container
        if not artifact_id.startswith(EXPORT_ARTIFACT_PREFIX):
            raise HTTPException(status_code=404, detail="Export not found")
        data = c.artifacts.read_bytes(artifact_id)
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{artifact_id}"', "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/v1/workspaces/{workspace_id}/usage")
    def get_usage(workspace_id: str, since: datetime | None = None) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            ws_runs = app.state.repo_runs.list_runs(workspace_id, limit=RUN_PAGE_MAX, since=since)
            run_totals = c.budget_ledger.get_runs_totals([r["run_id"] for r in ws_runs])
        except SQLAlchemyError as exc:
            logger.exception("Failed to query runs for usage from DB")
            raise _db_unavailable("load usage") from exc
        totals = [
            {
                **run_totals[r["run_id"]],
                "mode": r.get("mode", "quick"),
                "status": r.get("status", "queued"),
                "created_at": r.get("created_at", ""),
                "question": str(r.get("question", ""))[:200],
                "error": r.get("error"),
            }
            for r in ws_runs
        ]
        return {"workspace_id": workspace_id, "run_count": len(ws_runs), "runs": totals}

    @app.put("/api/v1/workspaces/{workspace_id}/settings/provider-key")
    def save_provider_key(workspace_id: str, payload: SaveProviderKeyRequest) -> dict[str, str]:
        cipher = CredentialCipher()
        encrypted = cipher.encrypt(payload.api_key)
        try:
            app.state.repo_creds.save_credential(workspace_id, payload.provider_id.value, encrypted)
        except SQLAlchemyError as exc:
            logger.exception("Failed to persist provider key to DB")
            raise _db_unavailable("save the provider key") from exc
        app.state.encrypted_keys[f"{workspace_id}:{payload.provider_id.value}"] = encrypted
        app.state.undecryptable_keys.discard(f"{workspace_id}:{payload.provider_id.value}")
        c: AppContainer = app.state.container
        c.routing_service._api_keys[payload.provider_id] = payload.api_key
        return {"provider_id": payload.provider_id.value, "status": "configured"}

    return app
