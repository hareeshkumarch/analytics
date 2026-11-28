from dataclasses import asdict
import time
from datetime import datetime
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from app.application.ports.tools import summarize_tool_executions
from app.application.prompts import ALL_TEMPLATES
from app.bootstrap.container import AppContainer

from app.api.support import RUN_PAGE_MAX
from app.api.context import ApiContext
from app.application.services.usage_summary import bucket_usage, select_usage_rows, summarize_usage
from app.api.responses import ModelCallListOut, PromptTemplateListOut, TaskRecordListOut, ToolExecutionListOut, UsageOut
from app.domain.limits import PREVIEW_CHARS

logger = logging.getLogger(__name__)

# How long one workspace's usage window is reused. Short enough that a finished question shows up on the next poll.
USAGE_CACHE_SECONDS = 3.0


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable

    window_cache: dict[tuple[str, str], tuple[float, Any]] = {}

    def _window(workspace_id: str, since: datetime | None) -> Any:
        """Everything the usage page needs for one workspace and time window.

        The page polls every few seconds and re-asks on every sort or filter change, all of which work on
        the same window. Keep the last answer for a moment so those requests share one set of queries.
        """
        key = (workspace_id, since.isoformat() if since else "")
        now = time.monotonic()
        hit = window_cache.get(key)
        if hit and now - hit[0] < USAGE_CACHE_SECONDS:
            return hit[1]
        c: AppContainer = app.state.container
        rows = app.state.repo_runs.usage_rows(workspace_id, since)
        ledger: Any = c.budget_ledger
        executions: Any = c.tool_executions
        if hasattr(ledger, "usage_totals") and hasattr(executions, "usage_by_tool"):
            # The database totals each table for the window in one grouped query.
            loaded = (rows, ledger.usage_totals(workspace_id, since), ledger.usage_by_model(workspace_id, since), executions.usage_by_tool(workspace_id, since))
        else:
            ids = [r["run_id"] for r in rows]
            loaded = (rows, ledger.get_runs_totals(ids), ledger.get_model_usage(ids), summarize_tool_executions(executions.list_for_runs(ids)))
        for stale in [k for k, (stamp, _) in window_cache.items() if now - stamp >= USAGE_CACHE_SECONDS]:
            window_cache.pop(stale, None)
        window_cache[key] = (now, loaded)
        return loaded

    @router.get("/api/v1/workspaces/{workspace_id}/usage", response_model=UsageOut, response_model_exclude_unset=True)
    def get_usage(
        workspace_id: str,
        since: datetime | None = None,
        sort: Literal["when", "calls", "tokens", "cost"] = "when",
        order: Literal["asc", "desc"] = "desc",
        status: str | None = Query(None, max_length=32),
        mode: str | None = Query(None, max_length=16),
        q: str | None = Query(None, max_length=200),
        limit: int | None = Query(None, ge=1),
        offset: int = Query(0, ge=0),
        bucket: Literal["hour", "day"] = "day",
        tz_offset_minutes: int = Query(0, ge=-840, le=840),
    ) -> dict[str, Any]:
        try:
            rows, all_totals, models, tools = _window(workspace_id, since)
        except SQLAlchemyError as exc:
            logger.exception("Failed to query runs for usage from DB")
            raise _db_unavailable("load usage") from exc
        # Filter and sort over the whole window, then page: "most expensive" means of everything, not of the newest page.
        matched = select_usage_rows(rows, all_totals, status=status, mode=mode, q=q, sort=sort, descending=order == "desc")
        page = matched[offset : offset + min(limit or RUN_PAGE_MAX, RUN_PAGE_MAX)]
        empty = {"used_attempts": 0, "total_tokens": 0, "total_cost_usd": "0", "incomplete": False}
        totals = [
            {
                **empty,
                **all_totals.get(r["run_id"], {}),
                "run_id": r["run_id"],
                "mode": r["mode"],
                "status": r["status"],
                "created_at": r["created_at"],
                "question": r["question"][:PREVIEW_CHARS],
                "error": r["error"],
            }
            for r in page
        ]
        return {
            "workspace_id": workspace_id,
            "run_count": len(rows),
            "runs": totals,
            "runs_truncated": len(matched) > offset + len(page),
            "matched_count": len(matched),
            "summary": summarize_usage([(r["run_id"], r["mode"], r["status"]) for r in rows], all_totals),
            "buckets": bucket_usage(rows, all_totals, hourly=bucket == "hour", tz_offset_minutes=tz_offset_minutes),
            "models": models,
            "tools": tools,
        }

    @router.get(
        "/api/v1/workspaces/{workspace_id}/runs/{run_id}/tool-executions",
        response_model=ToolExecutionListOut,
        response_model_exclude_unset=True,
    )
    def list_tool_executions(workspace_id: str, run_id: str) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            run = app.state.repo_runs.get_run(run_id)
            if not run or run.get("workspace_id") != workspace_id:
                raise HTTPException(status_code=404, detail="Run not found in this workspace")
            executions = c.tool_executions.list_for_runs([run_id])
        except SQLAlchemyError as exc:
            logger.exception("Failed to load tool executions for run %s", run_id)
            raise _db_unavailable("load tool calls") from exc
        return {
            "run_id": run_id,
            "executions": [{**asdict(e), "created_at": e.created_at.isoformat()} for e in executions],
        }

    @router.get(
        "/api/v1/workspaces/{workspace_id}/runs/{run_id}/tasks",
        response_model=TaskRecordListOut,
        response_model_exclude_unset=True,
    )
    def list_run_tasks(workspace_id: str, run_id: str) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            run = app.state.repo_runs.get_run(run_id)
            if not run or run.get("workspace_id") != workspace_id:
                raise HTTPException(status_code=404, detail="Run not found in this workspace")
            records = c.task_store.list_for_run(run_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to load tasks for run %s", run_id)
            raise _db_unavailable("load the run's tasks") from exc
        return {
            "run_id": run_id,
            "tasks": [
                {
                    "task_id": r.task_id,
                    "plan_revision": r.plan_revision,
                    "kind": r.kind,
                    "objective": r.objective,
                    "acceptance_criteria": list(r.acceptance_criteria),
                    "dependencies": list(r.dependencies),
                    "status": r.status,
                    "attempt": r.attempt,
                    "input_fingerprint": r.input_fingerprint,
                    "version": r.version,
                    "evidence_ids": list(r.evidence_ids),
                    "error": r.error,
                    "budget": {"tokens": r.budget.tokens, "tool_calls": r.budget.tool_calls} if r.budget else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in records
            ],
        }

    @router.get(
        "/api/v1/workspaces/{workspace_id}/runs/{run_id}/model-calls",
        response_model=ModelCallListOut,
        response_model_exclude_unset=True,
    )
    def list_model_calls(workspace_id: str, run_id: str) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            run = app.state.repo_runs.get_run(run_id)
            if not run or run.get("workspace_id") != workspace_id:
                raise HTTPException(status_code=404, detail="Run not found in this workspace")
            attempts = c.budget_ledger.list_run_attempts(run_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to load model calls for run %s", run_id)
            raise _db_unavailable("load model calls") from exc
        keys = ("reservation_id", "role", "provider_id", "model_id", "prompt_id", "prompt_version", "settled", "reserved_tokens", "actual_tokens", "cached_input_tokens", "latency_ms")
        return {"run_id": run_id, "calls": [{k: getattr(a, k) for k in keys} for a in attempts]}

    @router.get("/api/v1/prompts", response_model=PromptTemplateListOut)
    def list_prompt_templates() -> dict[str, Any]:
        return {"templates": [{"template_id": t.template_id, "version": t.version} for t in ALL_TEMPLATES]}

    return router
