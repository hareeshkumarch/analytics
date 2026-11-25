from datetime import datetime
import logging
from typing import Any

from fastapi import APIRouter
from sqlalchemy.exc import SQLAlchemyError

from app.bootstrap.container import AppContainer

from app.api.support import RUN_PAGE_MAX
from app.api.context import ApiContext
from app.api.responses import UsageOut

logger = logging.getLogger(__name__)


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable

    @router.get("/api/v1/workspaces/{workspace_id}/usage", response_model=UsageOut, response_model_exclude_unset=True)
    def get_usage(workspace_id: str, since: datetime | None = None) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            ws_runs = app.state.repo_runs.list_runs(workspace_id, limit=RUN_PAGE_MAX, since=since)
            run_ids = [r["run_id"] for r in ws_runs]
            run_totals = c.budget_ledger.get_runs_totals(run_ids)
            models = c.budget_ledger.get_model_usage(run_ids)
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
        return {"workspace_id": workspace_id, "run_count": len(ws_runs), "runs": totals, "models": models}

    return router
