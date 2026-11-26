import asyncio
import logging
import os
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.persistence.crypto import CredentialCipher
from app.adapters.persistence.database import create_session_factory
from app.adapters.persistence.pg_repositories import (
    PgCredentialRepository,
    PgBudgetLedger,
    PgToolExecutionLedger,
    PgEventLog,
    PgEvidenceRepository,
    PgPinRepository,
    PgWorkspaceQuota,
    PgWorkspaceSettings,
    PgRoutingRepository,
    PgRunRepository,
    PgSnapshotRepository,
)
from app.bootstrap.tracing import instrument_app
from app.bootstrap.logging_setup import clean_request_id, configure_logging, request_id_var
from app.bootstrap.container import AppContainer, build_container
from app.domain.errors import (
    DomainError,
    SecurityPolicyError,
)
from app.domain.limits import (
    DEFAULT_GLOBAL_LIMITS,
)
from app.domain.providers import ProviderId

from app.api.context import ApiContext
from app.api import run_service
from app.api.routes import connectors, exports, pins, runs, settings, snapshots, system, usage

ALLOWED_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "testserver", "api", "edge"})
logger = logging.getLogger(__name__)
access_logger = logging.getLogger("ledgerline.access")


def validate_loopback_bind(host: str) -> None:
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise SecurityPolicyError(
            f"Refusing to bind auth-free POC to non-loopback host {host!r}; only 127.0.0.1 is permitted."
        )


def create_app(
    container: AppContainer | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    configure_logging()
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
    app.state.container.tool_executions = PgToolExecutionLedger(sf)
    app.state.repo_runs = PgRunRepository(sf)
    app.state.repo_events = PgEventLog(sf)
    app.state.repo_evidence = PgEvidenceRepository(sf)
    app.state.repo_creds = PgCredentialRepository(sf)
    app.state.repo_routing = PgRoutingRepository(sf)
    app.state.repo_snapshots = PgSnapshotRepository(sf)
    app.state.repo_pins = PgPinRepository(sf)
    app.state.repo_quota = PgWorkspaceQuota(sf)
    app.state.repo_workspace_settings = PgWorkspaceSettings(sf)

    app.state.runs = {}
    app.state.answers = {}
    app.state.events = {}
    app.state.evidence = {}
    app.state.event_queues = {}
    app.state.event_lock = threading.RLock()
    app.state.run_event_locks = {}
    app.state.bg_tasks = {}
    app.state.provider_probes = {}
    app.state.active_run_semaphore = asyncio.Semaphore(DEFAULT_GLOBAL_LIMITS.max_active_root_runs)
    app.state.snapshots = {}
    app.state.semantics = {}
    app.state.encrypted_keys = {}
    app.state.undecryptable_keys = set()
    app.state.routing_policies = {}

    def _load_saved_credentials() -> None:
        c: AppContainer = app.state.container
        try:
            cipher = CredentialCipher()
        except SecurityPolicyError:
            if os.environ.get("APP_MASTER_KEY", "").strip():
                raise
            logger.warning("APP_MASTER_KEY is not set; saved provider keys were not loaded.")
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

    allow_origin_regex = os.environ.get("CORS_ALLOW_ORIGIN_REGEX", "").strip() or None
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
        allow_origin_regex=allow_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
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

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        req_id = clean_request_id(request.headers.get("x-request-id")) or uuid.uuid4().hex[:12]
        token = request_id_var.set(req_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("%s %s failed", request.method, request.url.path)
            raise
        else:
            response.headers["X-Request-ID"] = req_id
            access_logger.info(
                "%s %s %s %dms", request.method, request.url.path, response.status_code, int((time.monotonic() - started) * 1000)
            )
            return response
        finally:
            request_id_var.reset(token)

    def _request_id() -> str:
        return request_id_var.get() or uuid.uuid4().hex[:12]

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        req_id = _request_id()
        return JSONResponse(status_code=exc.http_status, content=exc.to_envelope(req_id).to_dict())

    @app.exception_handler(RequestValidationError)
    async def pydantic_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        req_id = _request_id()
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

    instrument_app(app)
    ctx = ApiContext(app)
    run_service.register(ctx)
    for area in (system, connectors, snapshots, settings, runs, pins, exports, usage):
        app.include_router(area.register(ctx))
    return app
