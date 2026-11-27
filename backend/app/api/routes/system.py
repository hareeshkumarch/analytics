import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.responses import OpsSwitchListOut, OpsSwitchOut
from app.api.schemas import SetOpsSwitchRequest
from app.application.runtime_settings import uses_celery
from app.application.services.ops_controls import PROVIDER_PREFIX, RUNS_SWITCH
from app.application.services.ops_metrics import render_prometheus
from app.bootstrap.container import AppContainer
from app.domain.limits import (
    DEEP_BUDGET_LIMITS,
    DEFAULT_DATA_LIMITS,
    DEFAULT_GLOBAL_LIMITS,
    QUICK_BUDGET_LIMITS,
)
from app.domain.providers import ProviderId

from app.api.context import ApiContext

logger = logging.getLogger(__name__)

METRICS_WINDOW_SECONDS = 3600
STUCK_AFTER_SECONDS = 2 * max(QUICK_BUDGET_LIMITS.active_seconds, DEEP_BUDGET_LIMITS.active_seconds)
SWITCH_HISTORY_LIMIT = 50


def _ping_redis() -> None:
    import redis

    client = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    finally:
        client.close()


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable

    @router.get("/api/v1/health")
    def health() -> dict[str, Any]:
        c: AppContainer = app.state.container
        return {
            "status": "ok",
            "connectors": [f.value for f in c.connectors.supported_families()],
            "providers": [p.value for p in c.providers.supported_providers()],
            "limits": DEFAULT_DATA_LIMITS.to_safe_dict(),
            "input_limits": {
                "max_question_chars": DEFAULT_GLOBAL_LIMITS.max_question_chars,
                "max_clarification_chars": DEFAULT_GLOBAL_LIMITS.max_clarification_chars,
                "max_pin_title_chars": DEFAULT_GLOBAL_LIMITS.max_pin_title_chars,
            },
            "run_limits": {
                lim.mode: {
                    "total_model_attempts": lim.total_model_attempts,
                    "max_replans": lim.max_replans,
                    "active_seconds": lim.active_seconds,
                    "total_tokens": lim.total_tokens,
                }
                for lim in (QUICK_BUDGET_LIMITS, DEEP_BUDGET_LIMITS)
            },
        }

    @router.get("/api/v1/ready")
    async def ready() -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            await asyncio.to_thread(app.state.ops_metrics.ping)
            checks["database"] = "ok"
        except SQLAlchemyError:
            logger.exception("Readiness: database check failed")
            checks["database"] = "unavailable"
        if uses_celery():
            try:
                await asyncio.to_thread(_ping_redis)
                checks["redis"] = "ok"
            except Exception:
                logger.exception("Readiness: Redis check failed")
                checks["redis"] = "unavailable"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", "checks": checks})

    @router.get("/api/v1/metrics", response_class=PlainTextResponse)
    def metrics() -> Response:
        try:
            snapshot = app.state.ops_metrics.snapshot(
                now=datetime.now(timezone.utc), latency_window_seconds=METRICS_WINDOW_SECONDS, stuck_after_seconds=STUCK_AFTER_SECONDS
            )
            paused = app.state.ops_switches.state().paused
        except SQLAlchemyError as exc:
            logger.exception("Failed to collect metrics")
            raise _db_unavailable("collect metrics") from exc
        return PlainTextResponse(render_prometheus(snapshot, paused, METRICS_WINDOW_SECONDS), media_type="text/plain; version=0.0.4")

    def _switch_list() -> dict[str, Any]:
        store = app.state.ops_switches
        return {
            "switches": store.list_all(),
            "available": [RUNS_SWITCH, *(f"{PROVIDER_PREFIX}{p.value}" for p in ProviderId)],
            "history": store.history(SWITCH_HISTORY_LIMIT),
        }

    @router.get("/api/v1/ops/switches", response_model=OpsSwitchListOut)
    def list_switches() -> dict[str, Any]:
        try:
            return _switch_list()
        except SQLAlchemyError as exc:
            logger.exception("Failed to load operator switches")
            raise _db_unavailable("load operator switches") from exc

    @router.put("/api/v1/ops/switches/{switch_id}", response_model=OpsSwitchOut)
    def set_switch(switch_id: str, payload: SetOpsSwitchRequest) -> dict[str, Any]:
        try:
            changed: dict[str, Any] = app.state.ops_switches.set(switch_id, payload.paused, payload.reason.strip())
        except SQLAlchemyError as exc:
            logger.exception("Failed to save operator switch %s", switch_id)
            raise _db_unavailable("save the operator switch") from exc
        logger.warning("Operator switch %s set to %s: %s", switch_id, "paused" if payload.paused else "resumed", payload.reason.strip())
        return changed

    return router
