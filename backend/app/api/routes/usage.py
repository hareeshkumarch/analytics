from dataclasses import asdict
from datetime import datetime
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.application.ports.tools import summarize_tool_executions
from app.application.prompts import ALL_TEMPLATES
from app.bootstrap.container import AppContainer

from app.api.support import RUN_PAGE_MAX
from app.api.context import ApiContext
from app.api.responses import ModelCallListOut, PromptTemplateListOut, TaskRecordListOut, ToolExecutionListOut, UsageOut

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
            tools = summarize_tool_executions(c.tool_executions.list_for_runs(run_ids))
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
        return {"workspace_id": workspace_id, "run_count": len(ws_runs), "runs": totals, "models": models, "tools": tools}

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
