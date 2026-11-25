"""Execute a persisted investigation from a Celery worker."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
import uuid
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.adapters.persistence.crypto import CredentialCipher
from app.adapters.persistence.database import create_session_factory
from app.adapters.persistence.pg_repositories import (
    PgBudgetLedger,
    PgCredentialRepository,
    PgEventLog,
    PgEvidenceRepository,
    PgRoutingRepository,
    PgRunRepository,
    PgSnapshotRepository,
)
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.application.services.routing_service import routing_policy_from_saved
from app.bootstrap.container import build_container
from app.domain.events import EventType, RunEvent
from app.domain.errors import ClarificationRequired, public_message
from app.domain.models import RunMode, RunStatus
from app.domain.providers import ProviderId, RoutingPolicy

logger = logging.getLogger(__name__)
_worker_session_factory: sessionmaker[Session] | None = None
_worker_session_factory_lock = threading.Lock()


def get_worker_session_factory() -> sessionmaker[Session]:
    global _worker_session_factory
    with _worker_session_factory_lock:
        if _worker_session_factory is None:
            _worker_session_factory = create_session_factory()
        return _worker_session_factory


def dispose_worker_session_factory() -> None:
    global _worker_session_factory
    with _worker_session_factory_lock:
        if _worker_session_factory is not None:
            engine = _worker_session_factory.kw.get("bind")
            if engine is not None:
                engine.dispose()
            _worker_session_factory = None


def _policy_for_run(repo: PgRoutingRepository, container: Any, workspace_id: str) -> RoutingPolicy:
    return routing_policy_from_saved(repo.get_policy(workspace_id), container.default_routing_policy)


def _load_saved_credentials(container: Any, repo: PgCredentialRepository, workspace_id: str) -> None:
    try:
        cipher = CredentialCipher()
        credentials = repo.list_active_credentials(workspace_id)
    except Exception:
        logger.exception("Could not load saved provider credentials in worker")
        return
    for credential in credentials:
        provider = credential.get("provider")
        if provider not in ProviderId._value2member_map_:
            continue
        try:
            container.routing_service._api_keys[ProviderId(provider)] = cipher.decrypt(
                credential["encrypted_payload"]
            )
        except Exception:
            logger.exception("Could not decrypt a saved provider credential in worker")


def execute_persisted_run(run_id: str) -> dict[str, str]:
    container = build_container()
    session_factory = get_worker_session_factory()
    container.budget_ledger = PgBudgetLedger(session_factory)
    container.routing_service.set_budget_ledger(container.budget_ledger)

    runs = PgRunRepository(session_factory)
    events = PgEventLog(session_factory)
    evidence = PgEvidenceRepository(session_factory)
    credentials = PgCredentialRepository(session_factory)
    routing = PgRoutingRepository(session_factory)
    snapshots = PgSnapshotRepository(session_factory)
    run = runs.get_run(run_id)
    if run is None:
        raise ValueError(f"Run {run_id} does not exist")
    if run["status"] in {
        RunStatus.AWAITING_INPUT.value,
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }:
        return {"run_id": run_id, "status": str(run["status"]), "answer_id": str(run.get("answer_id") or "")}

    workspace_id = str(run["workspace_id"])
    snapshot_id = str(run.get("snapshot_id") or "")
    semantic_version_id = str(run.get("semantic_version_id") or "")
    snapshot = snapshots.get_snapshot(snapshot_id) if snapshot_id else None
    semantics = snapshots.get_semantics(snapshot_id) if snapshot_id else None
    policy = _policy_for_run(routing, container, workspace_id)
    _load_saved_credentials(container, credentials, workspace_id)
    if not runs.update_status(run_id, RunStatus.RUNNING, unless_cancelled=True):
        latest = runs.get_run(run_id) or run
        return {"run_id": run_id, "status": str(latest.get("status") or RunStatus.CANCELLED.value), "answer_id": ""}

    sequence = int(run.get("last_event_sequence") or 0)
    event_lock = threading.Lock()

    def emit(event: RunEvent) -> None:
        nonlocal sequence
        with event_lock:
            sequence += 1
            persisted = RunEvent(
                schema_version=event.schema_version,
                event_id=f"evt_{uuid.uuid4().hex[:10]}",
                sequence=sequence,
                workspace_id=workspace_id,
                run_id=run_id,
                type=event.type,
                occurred_at=datetime.now(timezone.utc),
                payload=dict(event.payload),
            )
            if persisted.type == EventType.RUN_STATUS:
                try:
                    status = RunStatus(str(persisted.payload.get("status") or RunStatus.RUNNING.value))
                except ValueError:
                    status = RunStatus.RUNNING
                stored = events.append(
                    persisted,
                    status=status,
                    only_if_not_cancelled=True,
                )
            elif persisted.type == EventType.RUN_FINISHED:
                final_status = str(persisted.payload.get("status") or RunStatus.COMPLETED.value)
                outcome = "answered" if final_status == RunStatus.COMPLETED.value else final_status
                stored = events.append(
                    persisted,
                    status=final_status,
                    outcome=outcome,
                    error=str(persisted.payload.get("error") or "") or None,
                    only_if_not_cancelled=True,
                )
            else:
                stored = events.append(persisted, only_if_not_cancelled=True)
            if stored is not None:
                sequence = stored.sequence

    def save_plan(plan: dict[str, Any]) -> None:
        runs.save_plan(run_id, plan)

    def is_cancelled() -> bool:
        current = runs.get_run(run_id)
        return current is None or current.get("status") == RunStatus.CANCELLED.value

    prior_events = events.list_after(run_id, 0)
    tool_events = [
        event
        for event in prior_events
        if event.type == EventType.TOOL_RESULT and isinstance(event.payload.get("tool"), str)
    ]
    prior_tool_invocations = len(tool_events)
    prior_analytical_calls = sum(event.payload.get("tool") == "run_query_ir" for event in tool_events)

    def load_task_history(task_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        task_trace = [
            {key: value for key, value in event.payload.items() if key != "task_id"}
            for event in prior_events
            if event.type == EventType.TOOL_RESULT and event.payload.get("task_id") == task_id
        ]
        task_evidence = [
            item for item in evidence.list_run(run_id) if item.get("task_id") == task_id
        ]
        return task_trace, task_evidence

    orchestrator = InvestigationOrchestrator(
        container,
        query_engine=SnapshotQueryEngine(container.artifacts, SqlGlotQueryCompiler()),
    )
    orchestration_state = runs.get_orchestration_state(run_id)
    try:
        answer = orchestrator.execute_local(
            run_id=run_id,
            workspace_id=workspace_id,
            question=str(run.get("question") or ""),
            mode=RunMode(str(run.get("mode") or RunMode.QUICK.value)),
            snapshot_id=snapshot_id,
            semantic_version_id=semantic_version_id,
            snapshot=snapshot,
            semantics=semantics,
            policy_override=policy,
            emit_event=emit,
            should_cancel=is_cancelled,
            on_evidence=lambda item: evidence.save(run_id, workspace_id, item),
            initial_tool_invocations=prior_tool_invocations,
            initial_analytical_calls=prior_analytical_calls,
            evidence_lookup=lambda evidence_id: evidence.get(run_id, evidence_id),
            task_history_loader=load_task_history,
            clarification_answer=orchestration_state.get("clarification_answer"),
            initial_plan=orchestration_state.get("plan"),
            on_plan=save_plan,
        )
    except ClarificationRequired as exc:
        runs.pause_for_clarification(run_id, exc.prompt)
        latest = runs.get_run(run_id) or run
        if latest.get("status") == RunStatus.AWAITING_INPUT.value:
            return {
                "run_id": run_id,
                "status": RunStatus.AWAITING_INPUT.value,
                "answer_id": "",
            }
        return {"run_id": run_id, "status": str(latest.get("status") or RunStatus.CANCELLED.value), "answer_id": ""}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Worker execution failed for run %s", run_id)
        if not is_cancelled():
            with event_lock:
                sequence += 1
                failure = RunEvent(
                    schema_version=1,
                    event_id=f"evt_{uuid.uuid4().hex[:10]}",
                    sequence=sequence,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    type=EventType.RUN_FINISHED,
                    occurred_at=datetime.now(timezone.utc),
                    payload={"status": RunStatus.FAILED.value, "error": public_message(exc)},
                )
                events.append(
                    failure,
                    status=RunStatus.FAILED,
                    outcome="failed",
                    error=public_message(exc),
                    only_if_not_cancelled=True,
                )
        latest = runs.get_run(run_id) or run
        return {"run_id": run_id, "status": str(latest.get("status") or RunStatus.FAILED.value), "answer_id": ""}

    latest = runs.get_run(run_id) or run
    final_status = str(latest.get("status") or RunStatus.COMPLETED.value)
    if final_status == RunStatus.CANCELLED.value:
        return {"run_id": run_id, "answer_id": "", "status": final_status}
    if final_status not in {RunStatus.COMPLETED.value, RunStatus.FAILED.value}:
        final_status = RunStatus.COMPLETED.value
        with event_lock:
            sequence += 1
            finished = RunEvent(
                schema_version=1,
                event_id=f"evt_{uuid.uuid4().hex[:10]}",
                sequence=sequence,
                workspace_id=workspace_id,
                run_id=run_id,
                type=EventType.RUN_FINISHED,
                occurred_at=datetime.now(timezone.utc),
                payload={"status": final_status},
            )
            persisted = events.append(
                finished,
                status=final_status,
                outcome="answered",
                only_if_not_cancelled=True,
            )
            if persisted is None:
                latest = runs.get_run(run_id) or latest
                return {
                    "run_id": run_id,
                    "answer_id": "",
                    "status": str(latest.get("status") or RunStatus.CANCELLED.value),
                }
    if not runs.save_answer(answer, final_status=final_status):
        latest = runs.get_run(run_id) or latest
        return {
            "run_id": run_id,
            "answer_id": "",
            "status": str(latest.get("status") or RunStatus.CANCELLED.value),
        }
    return {"run_id": run_id, "answer_id": answer.answer_id, "status": final_status}
