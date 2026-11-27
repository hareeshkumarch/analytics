import asyncio
from decimal import Decimal
from datetime import datetime, timezone
import hashlib
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.application.runtime_settings import ExecutionMode, uses_celery
from app.application.runtime_settings import execution_mode as execution_mode_setting
from app.domain.events import RunEvent
from app.api.schemas import (
    ClarifyRunRequest,
    CreateRunRequest,
)
from app.domain.errors import (
    BudgetExhaustedError,
)
from app.domain.limits import (
    DEFAULT_GLOBAL_LIMITS,
)
from app.domain.models import RunStatus

from app.api import support
from app.api.support import start_of_business_day, workspace_daily_quota
from app.api.support import TERMINAL_RUN_STATUSES, SSE_HEARTBEAT_SECONDS, SSE_WORKER_POLL_SECONDS, RUN_PAGE_DEFAULT, RUN_PAGE_MAX, RUN_LIST_ANSWER_KEYS
from app.api.context import ApiContext
from app.jobs.outbox import dispatch_pending
from app.api.responses import CreateRunOut, RunOut, RunPageOut

logger = logging.getLogger(__name__)


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable
    _run_lock = ctx.run_lock
    _publish_event = ctx.publish_event
    _publish_api_event = ctx.publish_api_event
    _broadcast_persisted_event = ctx.broadcast_persisted_event
    _resolve_binding = ctx.resolve_binding

    @router.post("/api/v1/workspaces/{workspace_id}/runs", status_code=202, response_model=CreateRunOut, response_model_exclude_unset=True)
    async def create_run(
        workspace_id: str,
        payload: CreateRunRequest,
        idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        canonical_dict = payload.model_dump(mode="json")
        payload_hash = hashlib.sha256(json.dumps(canonical_dict, sort_keys=True).encode("utf-8")).hexdigest()
        idem_key = hashlib.sha256(f"{workspace_id}\0{idempotency_key}".encode()).hexdigest() if idempotency_key else None
        previous = None
        if idem_key:
            try:
                previous = await asyncio.to_thread(
                    app.state.repo_runs.get_idempotent_response, idem_key, DEFAULT_GLOBAL_LIMITS.idempotency_key_retention_hours
                )
            except SQLAlchemyError as exc:
                logger.exception("Could not read idempotency key")
                raise _db_unavailable("check the Idempotency-Key") from exc
        if previous:
            prev_hash, prev_code, prev_resp = previous
            if prev_hash != payload_hash:
                raise HTTPException(status_code=409, detail="Idempotency-Key reused with different payload")
            return JSONResponse(status_code=prev_code, content=prev_resp)

        execution_mode = execution_mode_setting()
        if execution_mode == ExecutionMode.CELERY and app.state.database_dialect != "postgresql":
            raise HTTPException(
                status_code=503,
                detail="Celery run execution requires PostgreSQL-backed persistence.",
            )
        try:
            await asyncio.to_thread(app.state.ops_switches.ensure_runs_allowed)
            queued_count = await asyncio.to_thread(app.state.repo_runs.count_queued_runs)
        except SQLAlchemyError as exc:
            logger.exception("Could not count queued runs in DB")
            raise _db_unavailable("check the run queue") from exc
        if queued_count >= DEFAULT_GLOBAL_LIMITS.max_queued_runs:
            raise BudgetExhaustedError(
                f"Global queue limit ({DEFAULT_GLOBAL_LIMITS.max_queued_runs} queued runs) reached."
            )
        daily_runs, daily_cost = workspace_daily_quota()
        if daily_runs is not None or daily_cost is not None:
            try:
                tz = await asyncio.to_thread(app.state.repo_workspace_settings.get_business_timezone, workspace_id)
                today = await asyncio.to_thread(app.state.repo_runs.run_ids_since, workspace_id, start_of_business_day(tz))
                totals = await asyncio.to_thread(app.state.container.budget_ledger.get_runs_totals, today) if daily_cost is not None else {}
            except SQLAlchemyError as exc:
                logger.exception("Could not check the workspace quota")
                raise _db_unavailable("check the workspace quota") from exc
            if daily_runs is not None and len(today) >= daily_runs:
                raise BudgetExhaustedError(
                    f"This workspace reached its daily limit of {daily_runs} questions.",
                    remediation="Try again tomorrow or raise WORKSPACE_DAILY_RUN_LIMIT.",
                )
            spent = sum((Decimal(str(t.get("total_cost_usd") or 0)) for t in totals.values()), Decimal(0))
            if daily_cost is not None and spent >= daily_cost:
                raise BudgetExhaustedError(
                    f"This workspace reached its daily spending limit of ${daily_cost}.",
                    remediation="Try again tomorrow or raise WORKSPACE_DAILY_COST_LIMIT_USD.",
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
                enqueue=execution_mode == ExecutionMode.CELERY and payload.background_async and not payload.execute_inline,
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
            await ctx.execute_run_record(run_rec)

        if payload.execute_inline:
            await _execute_bounded_run()
        elif payload.background_async:
            if execution_mode == ExecutionMode.CELERY:
                await asyncio.to_thread(dispatch_pending, app.state.repo_runs)
            else:
                app.state.bg_tasks[run_id] = asyncio.create_task(_execute_bounded_run())

        resp_body = {
            "run_id": run_id,
            "status": run_rec["status"],
            "status_url": f"/api/v1/workspaces/{workspace_id}/runs/{run_id}",
            "events_url": f"/api/v1/workspaces/{workspace_id}/runs/{run_id}/events",
        }
        if idem_key:
            try:
                await asyncio.to_thread(
                    app.state.repo_runs.save_idempotent_response,
                    idem_key,
                    payload_hash,
                    202,
                    resp_body,
                    DEFAULT_GLOBAL_LIMITS.idempotency_key_retention_hours,
                )
            except SQLAlchemyError:
                logger.exception("Could not save idempotency key for run %s", run_id)
        return JSONResponse(status_code=202, content=resp_body)

    @router.get("/api/v1/workspaces/{workspace_id}/runs", response_model=RunPageOut, response_model_exclude_unset=True)
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

    @router.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}", response_model=RunOut, response_model_exclude_unset=True)
    def get_run(workspace_id: str, run_id: str) -> dict[str, Any]:
        run_rec = app.state.repo_runs.get_run(run_id)
        if run_rec:
            app.state.runs[run_id] = run_rec
        else:
            run_rec = app.state.runs.get(run_id)
        if not run_rec or run_rec["workspace_id"] != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found")
        return run_rec

    @router.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}/evidence")
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

    @router.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}/evidence/{evidence_id}")
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

    @router.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}/events")
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
                last_sent = stream_started = time.monotonic()
                while live and not finished:
                    if time.monotonic() - stream_started > support.SSE_MAX_STREAM_SECONDS:
                        logger.warning("Closing SSE stream for run %s: maximum stream age reached", run_id)
                        break
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

    @router.post("/api/v1/workspaces/{workspace_id}/runs/{run_id}/clarify")
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
            enqueue=uses_celery(),
        )
        if updated is None or event is None:
            raise HTTPException(status_code=409, detail="Clarification revision conflict")
        run_rec.update(updated)
        app.state.runs[run_id] = run_rec
        _broadcast_persisted_event(event)

        if uses_celery():
            await asyncio.to_thread(dispatch_pending, app.state.repo_runs)
        else:
            app.state.bg_tasks[run_id] = asyncio.create_task(ctx.execute_run_record(run_rec))
        return {
            "run_id": run_id,
            "status": run_rec["status"],
            "revision": run_rec["clarification_revision"],
        }

    @router.post("/api/v1/workspaces/{workspace_id}/runs/{run_id}/cancel")
    async def cancel_run(workspace_id: str, run_id: str) -> dict[str, Any]:
        run_rec = await asyncio.to_thread(app.state.repo_runs.get_run, run_id)
        if run_rec:
            app.state.runs[run_id] = run_rec
        else:
            run_rec = app.state.runs.get(run_id)
        if not run_rec or run_rec["workspace_id"] != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found")
        with _run_lock(run_id):
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

    @router.delete("/api/v1/workspaces/{workspace_id}/runs/{run_id}")
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
        for store in (app.state.runs, app.state.answers, app.state.events, app.state.evidence, app.state.run_event_locks):
            store.pop(run_id, None)
        return {"run_id": run_id, "deleted": True}

    ctx.create_run = create_run
    return router
