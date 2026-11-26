import asyncio
import logging
import os
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.logging_setup import run_id_var
from app.domain.errors import (
    ClarificationRequired,
    public_message,
)
from app.domain.models import RunMode, RunStatus

from app.api.context import ApiContext
from app.jobs.outbox import dispatch_pending

logger = logging.getLogger(__name__)

OUTBOX_POLL_SECONDS = 5.0


def register(ctx: ApiContext) -> None:
    app = ctx.app
    _store_evidence = ctx.store_evidence
    _prior_tool_usage = ctx.prior_tool_usage
    _load_task_history = ctx.load_task_history
    _get_query_engine = ctx.get_query_engine
    _publish_event = ctx.publish_event
    _publish_api_event = ctx.publish_api_event
    _broadcast_persisted_event = ctx.broadcast_persisted_event
    _policy_for_workspace = ctx.policy_for_workspace

    async def _execute_run_record(run_rec: dict[str, Any]) -> None:
        current_task = asyncio.current_task()
        run_id = str(run_rec["run_id"])
        workspace_id = str(run_rec["workspace_id"])
        snapshot_id = str(run_rec.get("snapshot_id") or "")
        semantic_version_id = str(run_rec.get("semantic_version_id") or "")

        def _is_cancelled() -> bool:
            return run_rec.get("status") == RunStatus.CANCELLED.value

        run_token = run_id_var.set(run_id)
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
                    business_timezone=app.state.repo_workspace_settings.get_business_timezone(workspace_id),
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
                run_rec["outcome"] = answer.outcome if final_status == RunStatus.COMPLETED.value else final_status
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
            run_id_var.reset(run_token)
            if app.state.bg_tasks.get(run_id) is current_task:
                app.state.bg_tasks.pop(run_id, None)

    async def _dispatch_outbox_forever() -> None:
        while True:
            try:
                await asyncio.to_thread(dispatch_pending, app.state.repo_runs)
            except Exception:
                logger.exception("Outbox dispatch failed")
            await asyncio.sleep(OUTBOX_POLL_SECONDS)

    @app.on_event("shutdown")
    async def _stop_outbox_dispatcher() -> None:
        task = getattr(app.state, "outbox_task", None)
        if task is not None:
            task.cancel()

    @app.on_event("startup")
    async def _resume_active_runs() -> None:
        if os.environ.get("RUN_EXECUTION_MODE", "inline").strip().lower() == "celery":
            app.state.outbox_task = asyncio.create_task(_dispatch_outbox_forever())
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

    ctx.execute_run_record = _execute_run_record
