import logging
from typing import Any

from fastapi import APIRouter

from app.bootstrap.container import AppContainer
from app.domain.limits import (
    DEEP_BUDGET_LIMITS,
    DEFAULT_DATA_LIMITS,
    QUICK_BUDGET_LIMITS,
)

from app.api.context import ApiContext

logger = logging.getLogger(__name__)


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app

    @router.get("/api/v1/health")
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

    return router
