import asyncio
from datetime import datetime, timezone
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.domain.events import EventType, RunEvent
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.services.routing_service import routing_policy_from_saved
from app.domain.errors import (
    PersistenceUnavailableError,
)
from app.domain.models import RunStatus
from app.domain.providers import RoutingPolicy

from app.api.support import MAX_RUNS_WITH_EVIDENCE

logger = logging.getLogger(__name__)


async def _unregistered(*_: Any, **__: Any) -> Any:
    raise RuntimeError("This handler is registered with the API router at startup.")


class ApiContext:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.create_run: Callable[..., Awaitable[Any]] = _unregistered
        self.approve_semantics: Callable[..., Awaitable[dict[str, Any]]] = _unregistered
        self.auto_refresh_pins: Callable[[str, str], Awaitable[list[str]]] = _unregistered
        self.execute_run_record: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] = _unregistered
        self._bind_helpers()

    def _bind_helpers(self) -> None:
        app = self.app

        def _db_unavailable(action: str) -> PersistenceUnavailableError:
            return PersistenceUnavailableError(
                f"Could not {action} because the database is unavailable.",
                remediation="Check that PostgreSQL is running and DATABASE_URL is correct, then try again.",
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

        def _run_lock(run_id: str) -> threading.RLock:
            with app.state.event_lock:
                lock: threading.RLock | None = app.state.run_event_locks.get(run_id)
                if lock is None:
                    lock = threading.RLock()
                    app.state.run_event_locks[run_id] = lock
                return lock

        def _publish_event(run_id: str, evt_dict: dict[str, Any], *, skip_if_cancelled: bool = False) -> bool:
            # The per-run lock orders one run's events; the global lock only guards the shared maps, never database I/O.
            with _run_lock(run_id):
                with app.state.event_lock:
                    rec = app.state.runs.get(run_id)
                if rec is None and hasattr(app.state, "repo_runs"):
                    rec = app.state.repo_runs.get_run(run_id)
                    if rec:
                        with app.state.event_lock:
                            rec = app.state.runs.setdefault(run_id, rec)
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
                with app.state.event_lock:
                    if rec is not None:
                        rec["last_event_sequence"] = max(
                            int(rec.get("last_event_sequence") or 0), int(evt_dict["sequence"])
                        )
                    app.state.events.setdefault(run_id, []).append(evt_dict)
                    subscribers = list(app.state.event_queues.get(run_id, []))
                for loop, q in subscribers:
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

        async def _upload_bytes(workspace_id: str, config: dict[str, Any]) -> bytes | None:
            upload_id = config.get("upload_id")
            if upload_id is None:
                return None
            return await asyncio.to_thread(app.state.container.uploads.read, workspace_id, str(upload_id))

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

        def _policy_for_workspace(workspace_id: str) -> RoutingPolicy:
            policy = app.state.routing_policies.get(workspace_id)
            if policy is not None:
                return policy
            try:
                saved = app.state.repo_routing.get_policy(workspace_id)
            except SQLAlchemyError as exc:
                logger.exception("Could not load routing policy for workspace %s", workspace_id)
                raise _db_unavailable("load the routing policy and cost limit") from exc
            policy = routing_policy_from_saved(saved, app.state.container.default_routing_policy, workspace_id)
            app.state.routing_policies[workspace_id] = policy
            return policy

        def _snapshot_record(snapshot_id: str | None) -> dict[str, Any] | None:
            if not snapshot_id:
                return None
            snap: dict[str, Any] | None = app.state.snapshots.get(snapshot_id) or app.state.repo_snapshots.get_snapshot(snapshot_id)
            return snap

        def _newest_approved_in_source(workspace_id: str, source_id: str) -> dict[str, Any] | None:
            return next(
                (
                    s
                    for s in app.state.repo_snapshots.list_snapshots(workspace_id)
                    if s["source_id"] == source_id and s.get("approved")
                ),
                None,
            )

        self.db_unavailable = _db_unavailable
        self.store_evidence = _store_evidence
        self.prior_tool_usage = _prior_tool_usage
        self.load_task_history = _load_task_history
        self.get_query_engine = _get_query_engine
        self.get_workspace_snapshot = _get_workspace_snapshot
        self.run_lock = _run_lock
        self.publish_event = _publish_event
        self.publish_api_event = _publish_api_event
        self.broadcast_persisted_event = _broadcast_persisted_event
        self.upload_bytes = _upload_bytes
        self.semantics_for = _semantics_for
        self.resolve_binding = _resolve_binding
        self.policy_for_workspace = _policy_for_workspace
        self.snapshot_record = _snapshot_record
        self.newest_approved_in_source = _newest_approved_in_source
