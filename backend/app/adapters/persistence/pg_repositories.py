from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import uuid
from typing import Any

from sqlalchemy import case, delete, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.persistence.database import ensure_conversation, ensure_workspace
from app.adapters.persistence.orm_models import (
    BudgetReservationRow,
    CredentialRefRow,
    OutboxJobRow,
    PinRow,
    RoutingPolicyRow,
    RunEventRow,
    RunEvidenceRow,
    RunRow,
    SemanticVersionRow,
    SnapshotManifestRow,
    SourceRow,
)
from app.domain.errors import BudgetExhaustedError, NotFoundError, ValidationError, VersionConflictError
from app.domain.events import EventType, RunEvent
from app.domain.models import Answer, RunStatus
from app.domain.limits import DEEP_BUDGET_LIMITS, QUICK_BUDGET_LIMITS
from app.domain.providers import BudgetReservation


def _append_run_event(
    db: Session,
    row: RunRow,
    event_type: EventType,
    payload: dict[str, Any],
) -> RunEvent:
    sequence = int(row.last_event_sequence or 0) + 1
    event = RunEvent(
        schema_version=1,
        event_id=f"evt_{uuid.uuid4().hex[:10]}",
        sequence=sequence,
        workspace_id=row.workspace_id,
        run_id=row.run_id,
        type=event_type,
        occurred_at=datetime.now(timezone.utc),
        payload=payload,
    )
    db.add(
        RunEventRow(
            event_id=event.event_id,
            run_id=event.run_id,
            workspace_id=event.workspace_id,
            sequence=event.sequence,
            type=event.type.value,
            payload_json=dict(event.payload),
            occurred_at=event.occurred_at,
        )
    )
    row.last_event_sequence = sequence
    return event


class PgEventLog:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def append(
        self,
        event: RunEvent,
        *,
        status: RunStatus | str | None = None,
        outcome: str | None = None,
        error: str | None = None,
        only_if_not_cancelled: bool = False,
    ) -> RunEvent | None:
        with self._sf() as db:
            with db.begin():
                run = db.get(RunRow, event.run_id, with_for_update=True)
                if run is None:
                    raise ValueError(f"Run {event.run_id} does not exist")
                if only_if_not_cancelled and run.status == RunStatus.CANCELLED.value:
                    return None
                event = replace(event, sequence=(run.last_event_sequence or 0) + 1)
                row = RunEventRow(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    workspace_id=event.workspace_id,
                    sequence=event.sequence,
                    type=event.type.value if hasattr(event.type, "value") else str(event.type),
                    payload_json=dict(event.payload),
                    occurred_at=event.occurred_at,
                )
                db.add(row)
                run.last_event_sequence = event.sequence
                if status is not None:
                    run.status = status.value if hasattr(status, "value") else str(status)
                if outcome is not None:
                    run.outcome = outcome
                if error:
                    run.answer_json = {**(run.answer_json or {}), "error": error}
        return event

    def list_after(self, run_id: str, after_sequence: int) -> tuple[RunEvent, ...]:
        with self._sf() as db:
            rows = db.scalars(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id, RunEventRow.sequence > after_sequence)
                .order_by(RunEventRow.sequence.asc())
            ).all()
            return tuple(
                RunEvent(
                    schema_version=1,
                    event_id=r.event_id,
                    sequence=r.sequence,
                    workspace_id=r.workspace_id,
                    run_id=r.run_id,
                    type=EventType(r.type) if r.type in EventType._value2member_map_ else EventType.RUN_STATUS,
                    occurred_at=r.occurred_at,
                    payload=r.payload_json,
                )
                for r in rows
            )


class PgEvidenceRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def save(self, run_id: str, workspace_id: str, evidence: dict[str, Any]) -> None:
        evidence_id = str(evidence.get("evidence_id") or "")
        if not evidence_id:
            return
        task_id = str(evidence.get("task_id") or "unknown")
        with self._sf() as db:
            with db.begin():
                row = db.scalars(
                    select(RunEvidenceRow)
                    .where(
                        RunEvidenceRow.run_id == run_id,
                        RunEvidenceRow.evidence_id == evidence_id,
                        RunEvidenceRow.task_id == task_id,
                    )
                    .with_for_update()
                ).first()
                if row is None:
                    row = RunEvidenceRow(
                        evidence_record_id=f"evr_{uuid.uuid4().hex[:20]}",
                        run_id=run_id,
                        evidence_id=evidence_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        evidence_json=dict(evidence),
                    )
                    db.add(row)
                else:
                    row.task_id = task_id
                    row.evidence_json = dict(evidence)

    def get(self, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        with self._sf() as db:
            row = db.scalars(
                select(RunEvidenceRow).where(
                    RunEvidenceRow.run_id == run_id,
                    RunEvidenceRow.evidence_id == evidence_id,
                )
            ).first()
            return dict(row.evidence_json) if row else None

    def list_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._sf() as db:
            rows = db.scalars(
                select(RunEvidenceRow)
                .where(RunEvidenceRow.run_id == run_id)
                .order_by(RunEvidenceRow.created_at.asc())
            ).all()
            return [dict(row.evidence_json) for row in rows]


class PgRunRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def create_run(
        self,
        run_id: str,
        workspace_id: str,
        conversation_id: str,
        mode: str,
        status: str,
        question: str,
        snapshot_id: str | None = None,
        semantic_version_id: str | None = None,
        policy_hash: str = "default",
    ) -> dict[str, Any]:
        with self._sf() as db:
            with db.begin():
                ensure_conversation(db, workspace_id, conversation_id, title=question[:200])
                now = datetime.now(timezone.utc)
                row = RunRow(
                    run_id=run_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    mode=mode,
                    status=status,
                    outcome=None,
                    snapshot_id=snapshot_id,
                    semantic_version_id=semantic_version_id,
                    policy_hash=policy_hash,
                    last_event_sequence=1,
                    owner_generation=1,
                    answer_json={"question": question},
                    orchestration_json={"clarification_revision": 0},
                    created_at=now,
                )
                db.add(row)
            return {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "mode": mode,
                "status": status,
                "outcome": None,
                "snapshot_id": snapshot_id,
                "semantic_version_id": semantic_version_id,
                "question": question,
                "last_event_sequence": 1,
                "created_at": now.isoformat(),
                "clarification_revision": 0,
                "clarification_prompt": None,
                "clarification_answer": None,
            }

    def fail_interrupted_runs(self) -> list[str]:
        """Mark runs left queued/running by a previous API process as failed and close their event streams.

        Runs execute inside the API process, so at startup none of them can still be working.
        """
        closed: list[str] = []
        with self._sf() as db:
            with db.begin():
                rows = db.scalars(select(RunRow).where(RunRow.status.in_(("queued", "running")))).all()
                now = datetime.now(timezone.utc)
                for row in rows:
                    seq = (row.last_event_sequence or 0) + 1
                    row.status = "failed"
                    row.outcome = "interrupted"
                    row.last_event_sequence = seq
                    row.answer_json = {**(row.answer_json or {}), "error": "The API restarted while this run was in progress."}
                    db.add(
                        RunEventRow(
                            event_id=f"evt_{uuid.uuid4().hex[:16]}",
                            run_id=row.run_id,
                            workspace_id=row.workspace_id,
                            sequence=seq,
                            type="run.finished",
                            payload_json={"status": "failed", "error": "The API restarted while this run was in progress."},
                            occurred_at=now,
                        )
                    )
                    closed.append(row.run_id)
        return closed

    def list_active_runs(self) -> list[dict[str, Any]]:
        with self._sf() as db:
            run_ids = db.scalars(
                select(RunRow.run_id).where(RunRow.status.in_(("queued", "running")))
            ).all()
        return [run for run_id in run_ids if (run := self.get_run(run_id)) is not None]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._sf() as db:
            row = db.get(RunRow, run_id)
            if not row:
                return None
            ans = row.answer_json or {}
            state = row.orchestration_json or {}
            return {
                "run_id": row.run_id,
                "workspace_id": row.workspace_id,
                "conversation_id": row.conversation_id,
                "mode": row.mode,
                "status": row.status,
                "outcome": row.outcome,
                "snapshot_id": row.snapshot_id,
                "semantic_version_id": row.semantic_version_id,
                "last_event_sequence": row.last_event_sequence,
                "owner_generation": row.owner_generation,
                "question": ans.get("question", ""),
                "answer": ans,
                "clarification_revision": int(state.get("clarification_revision", 0)),
                "clarification_prompt": state.get("clarification_prompt"),
                "clarification_answer": state.get("clarification_answer"),
                "answer_id": ans.get("answer_id"),
                "error": ans.get("error"),
                "created_at": row.created_at.isoformat(),
            }

    def list_runs(
        self,
        workspace_id: str,
        limit: int | None = None,
        before: datetime | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        with self._sf() as db:
            stmt = select(RunRow).where(RunRow.workspace_id == workspace_id)
            if before is not None:
                stmt = stmt.where(RunRow.created_at < before)
            if since is not None:
                stmt = stmt.where(RunRow.created_at >= since)
            stmt = stmt.order_by(RunRow.created_at.desc(), RunRow.run_id.desc())
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = db.scalars(stmt).all()
            res = []
            for r in rows:
                ans = r.answer_json or {}
                state = r.orchestration_json or {}
                res.append(
                    {
                        "run_id": r.run_id,
                        "workspace_id": r.workspace_id,
                        "conversation_id": r.conversation_id,
                        "mode": r.mode,
                        "status": r.status,
                        "outcome": r.outcome,
                        "snapshot_id": r.snapshot_id,
                        "semantic_version_id": r.semantic_version_id,
                        "last_event_sequence": r.last_event_sequence,
                        "question": ans.get("question", ""),
                        "answer": ans,
                        "clarification_revision": int(state.get("clarification_revision", 0)),
                        "clarification_prompt": state.get("clarification_prompt"),
                        "clarification_answer": state.get("clarification_answer"),
                        "answer_id": ans.get("answer_id"),
                        "error": ans.get("error"),
                        "created_at": r.created_at.isoformat(),
                    }
                )
            return res

    def delete_run(self, workspace_id: str, run_id: str) -> bool:
        with self._sf() as db:
            with db.begin():
                run = db.get(RunRow, run_id, with_for_update=True)
                if run is None or run.workspace_id != workspace_id:
                    return False
                for model in (BudgetReservationRow, RunEventRow, RunEvidenceRow):
                    db.execute(delete(model).where(model.run_id == run_id))
                db.execute(delete(PinRow).where(PinRow.workspace_id == workspace_id, PinRow.run_id == run_id))
                checkpoint_tables = {"checkpoint_writes", "checkpoint_blobs", "checkpoints"} & set(
                    inspect(db.connection()).get_table_names()
                )
                for table in sorted(checkpoint_tables):
                    db.execute(
                        text(f"DELETE FROM {table} WHERE thread_id = :graph OR thread_id LIKE :tasks"),
                        {"graph": f"run:{run_id}", "tasks": f"{run_id}:%"},
                    )
                db.delete(run)
                return True

    def get_orchestration_state(self, run_id: str) -> dict[str, Any]:
        with self._sf() as db:
            row = db.get(RunRow, run_id)
            return dict(row.orchestration_json or {}) if row else {}

    def save_plan(self, run_id: str, plan: dict[str, Any]) -> bool:
        with self._sf() as db:
            with db.begin():
                row = db.get(RunRow, run_id, with_for_update=True)
                if not row or row.status == RunStatus.CANCELLED.value:
                    return False
                state = dict(row.orchestration_json or {})
                state["plan"] = plan
                row.orchestration_json = state
        return True

    def pause_for_clarification(self, run_id: str, prompt: str) -> tuple[RunEvent, ...]:
        with self._sf() as db:
            with db.begin():
                row = db.get(RunRow, run_id, with_for_update=True)
                if not row or row.status not in {RunStatus.QUEUED.value, RunStatus.RUNNING.value}:
                    return ()
                state = dict(row.orchestration_json or {})
                revision = int(state.get("clarification_revision", 0))
                state["clarification_prompt"] = prompt
                row.orchestration_json = state
                row.status = RunStatus.AWAITING_INPUT.value
                status_event = _append_run_event(
                    db,
                    row,
                    EventType.RUN_STATUS,
                    {"status": RunStatus.AWAITING_INPUT.value},
                )
                clarification_event = _append_run_event(
                    db,
                    row,
                    EventType.CLARIFICATION_REQUIRED,
                    {"prompt": prompt, "revision": revision},
                )
                return status_event, clarification_event

    def submit_clarification(
        self,
        run_id: str,
        expected_revision: int,
        answer: str,
    ) -> tuple[dict[str, Any] | None, RunEvent | None]:
        with self._sf() as db:
            with db.begin():
                row = db.get(RunRow, run_id, with_for_update=True)
                if not row or row.status != RunStatus.AWAITING_INPUT.value:
                    return None, None
                state = dict(row.orchestration_json or {})
                revision = int(state.get("clarification_revision", 0))
                if revision != expected_revision:
                    return None, None
                state["clarification_answer"] = answer
                state["last_clarification_prompt"] = state.get("clarification_prompt")
                state["clarification_prompt"] = None
                state["clarification_revision"] = revision + 1
                row.orchestration_json = state
                row.status = RunStatus.QUEUED.value
                row.outcome = None
                event = _append_run_event(
                    db,
                    row,
                    EventType.RUN_STATUS,
                    {"status": RunStatus.QUEUED.value, "revision": revision + 1},
                )
        return self.get_run(run_id), event

    def count_queued_runs(self) -> int:
        with self._sf() as db:
            return int(
                db.scalar(select(func.count()).select_from(RunRow).where(RunRow.status == RunStatus.QUEUED.value))
                or 0
            )

    def update_status(
        self,
        run_id: str,
        status: RunStatus | str,
        outcome: str | None = None,
        event: RunEvent | None = None,
        error: str | None = None,
        unless_cancelled: bool = False,
    ) -> bool:
        st_val = status.value if hasattr(status, "value") else str(status)
        with self._sf() as db:
            with db.begin():
                row = db.get(RunRow, run_id, with_for_update=True)
                if not row:
                    return False
                if unless_cancelled and row.status == RunStatus.CANCELLED.value:
                    return False
                row.status = st_val
                if outcome is not None:
                    row.outcome = outcome
                if error:
                    ans = dict(row.answer_json or {})
                    ans["error"] = error
                    row.answer_json = ans
                if event is not None:
                    db.add(
                        RunEventRow(
                            event_id=event.event_id,
                            run_id=event.run_id,
                            workspace_id=event.workspace_id,
                            sequence=event.sequence,
                            type=event.type.value if hasattr(event.type, "value") else str(event.type),
                            payload_json=dict(event.payload),
                            occurred_at=event.occurred_at,
                        )
                    )
                    row.last_event_sequence = event.sequence
        return True

    def enqueue_outbox_job(self, job_type: str, payload: dict[str, Any]) -> str:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        with self._sf() as db:
            with db.begin():
                db.add(
                    OutboxJobRow(
                        job_id=job_id,
                        job_type=job_type,
                        payload_json=payload,
                        status="pending",
                    )
                )
        return job_id

    def save_answer(self, answer: Answer, final_status: str = "completed") -> bool:
        with self._sf() as db:
            with db.begin():
                row = db.get(RunRow, answer.run_id, with_for_update=True)
                if not row or row.status == RunStatus.CANCELLED.value:
                    return False
                existing_ans = dict(row.answer_json or {})
                existing_ans.update(
                    {
                        "answer_id": answer.answer_id,
                        "summary_markdown": answer.summary_markdown,
                        "limitations": list(answer.limitations),
                        "snapshot_id": answer.snapshot_id,
                        "semantic_version_id": answer.semantic_version_id,
                        "claims": [
                            {
                                "claim_id": c.claim_id,
                                "label": c.label,
                                "statement": c.statement,
                                "value": str(c.value) if c.value is not None else None,
                                "unit": c.unit,
                                "currency": c.currency,
                                "evidence_ids": list(c.evidence_ids),
                            }
                            for c in answer.claims
                        ],
                    }
                )
                row.answer_json = existing_ans
                row.status = final_status
                row.outcome = "answered" if final_status == "completed" else final_status
        return True


class PgCredentialRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def save_credential(self, workspace_id: str, provider: str, encrypted_payload: str) -> None:
        with self._sf() as db:
            with db.begin():
                ensure_workspace(db, workspace_id)
                stmt = select(CredentialRefRow).where(
                    CredentialRefRow.workspace_id == workspace_id,
                    CredentialRefRow.provider_or_source == provider,
                )
                existing = db.scalars(stmt).first()
                if existing:
                    existing.encrypted_payload = encrypted_payload
                    existing.revoked = False
                    existing.generation += 1
                else:
                    cred_id = f"cred_{uuid.uuid4().hex[:12]}"
                    db.add(
                        CredentialRefRow(
                            credential_id=cred_id,
                            workspace_id=workspace_id,
                            provider_or_source=provider,
                            generation=1,
                            encrypted_payload=encrypted_payload,
                            revoked=False,
                        )
                    )

    def list_active_credentials(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with self._sf() as db:
            stmt = select(CredentialRefRow).where(CredentialRefRow.revoked.is_(False))
            if workspace_id:
                stmt = stmt.where(CredentialRefRow.workspace_id == workspace_id)
            rows = db.scalars(stmt).all()
            return [
                {
                    "credential_id": r.credential_id,
                    "workspace_id": r.workspace_id,
                    "provider": r.provider_or_source,
                    "generation": r.generation,
                    "encrypted_payload": r.encrypted_payload,
                }
                for r in rows
            ]

    def delete_credential(self, workspace_id: str, provider: str) -> None:
        with self._sf() as db:
            with db.begin():
                stmt = select(CredentialRefRow).where(
                    CredentialRefRow.workspace_id == workspace_id,
                    CredentialRefRow.provider_or_source == provider,
                )
                row = db.scalars(stmt).first()
                if row:
                    row.revoked = True


class PgRoutingRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def get_policy(self, workspace_id: str) -> dict[str, Any] | None:
        with self._sf() as db:
            stmt = (
                select(RoutingPolicyRow)
                .where(RoutingPolicyRow.workspace_id == workspace_id)
                .order_by(RoutingPolicyRow.policy_version.desc())
            )
            row = db.scalars(stmt).first()
            if not row:
                return None
            return {
                "policy_version": row.policy_version,
                "workspace_id": row.workspace_id,
                **row.policy_json,
            }

    def save_policy(
        self,
        workspace_id: str,
        policy_dict: dict[str, Any],
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        try:
            with self._sf() as db:
                with db.begin():
                    ensure_workspace(db, workspace_id)
                    stmt = (
                        select(RoutingPolicyRow)
                        .where(RoutingPolicyRow.workspace_id == workspace_id)
                        .order_by(RoutingPolicyRow.policy_version.desc())
                    )
                    current = db.scalars(stmt).first()
                    if current and expected_version is not None and current.policy_version != expected_version:
                        raise VersionConflictError("Routing policy version conflict")

                    next_ver = (current.policy_version + 1) if current else ((expected_version or 1) + 1)
                    row = RoutingPolicyRow(
                        policy_version=next_ver,
                        workspace_id=workspace_id,
                        policy_json=policy_dict,
                    )
                    db.add(row)
                    db.flush()
                    return {
                        "policy_version": row.policy_version,
                        "workspace_id": row.workspace_id,
                        **row.policy_json,
                    }
        except IntegrityError as exc:
            # A concurrent save took the same (workspace_id, policy_version); report it as a version conflict.
            raise VersionConflictError("Routing policy version conflict") from exc


class PgSnapshotRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def reserve_snapshot_version(
        self,
        workspace_id: str,
        source_id: str,
        source_family: str,
        source_name: str,
        source_config: dict[str, Any],
    ) -> int:
        try:
            return self._increment_source_version(workspace_id, source_id, source_family, source_name, source_config)
        except IntegrityError:
            return self._increment_source_version(workspace_id, source_id, source_family, source_name, source_config)

    def _increment_source_version(
        self,
        workspace_id: str,
        source_id: str,
        source_family: str,
        source_name: str,
        source_config: dict[str, Any],
    ) -> int:
        with self._sf() as db:
            with db.begin():
                version = db.execute(
                    update(SourceRow)
                    .where(SourceRow.source_id == source_id, SourceRow.workspace_id == workspace_id)
                    .values(last_version=SourceRow.last_version + 1)
                    .returning(SourceRow.last_version)
                ).scalar_one_or_none()
                if version is not None:
                    return int(version)
                if db.get(SourceRow, source_id) is not None:
                    raise ValidationError("Source belongs to a different workspace.")
                ensure_workspace(db, workspace_id)
                db.add(
                    SourceRow(
                        source_id=source_id,
                        workspace_id=workspace_id,
                        family=source_family,
                        name=source_name,
                        config_json=source_config,
                        last_version=1,
                    )
                )
                db.flush()
                return 1

    def save_snapshot(
        self,
        workspace_id: str,
        source_id: str,
        snapshot_id: str,
        version: int,
        schema_fingerprint: str,
        manifest_json: dict[str, Any],
        artifact_id: str,
        sha256: str,
        source_config: dict[str, Any] | None = None,
        semantics_json: dict[str, Any] | None = None,
    ) -> None:
        with self._sf() as db:
            with db.begin():
                src = db.get(SourceRow, source_id)
                if src is None or src.workspace_id != workspace_id:
                    raise ValidationError("Reserve a snapshot version for this source before saving it.")
                src.current_snapshot_id = snapshot_id
                src.config_json = source_config or src.config_json
                db.flush()

                manifest_row = db.get(SnapshotManifestRow, snapshot_id)
                if not manifest_row:
                    manifest_row = SnapshotManifestRow(
                        snapshot_id=snapshot_id,
                        workspace_id=workspace_id,
                        source_id=source_id,
                        version=version,
                        schema_fingerprint=schema_fingerprint,
                        manifest_json=manifest_json,
                        artifact_id=artifact_id,
                        sha256=sha256,
                    )
                    db.add(manifest_row)
                    db.flush()

                if semantics_json:
                    sem_id = semantics_json.get("semantic_version_id") or f"sem_{uuid.uuid4().hex[:10]}"
                    sem_row = db.get(SemanticVersionRow, sem_id)
                    if not sem_row:
                        sem_row = SemanticVersionRow(
                            semantic_version_id=sem_id,
                            workspace_id=workspace_id,
                            snapshot_id=snapshot_id,
                            version=int(semantics_json.get("version", 1)),
                            approved=bool(semantics_json.get("approved", False)),
                            definitions_json=semantics_json,
                        )
                        db.add(sem_row)
                        db.flush()

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._sf() as db:
            snap = db.get(SnapshotManifestRow, snapshot_id)
            if not snap:
                return None
            res = dict(snap.manifest_json)
            res.update(
                {
                    "snapshot_id": snap.snapshot_id,
                    "workspace_id": snap.workspace_id,
                    "source_id": snap.source_id,
                    "version": snap.version,
                    "schema_fingerprint": snap.schema_fingerprint,
                    "artifact_id": snap.artifact_id,
                    "sha256": snap.sha256,
                    "created_at": snap.created_at.isoformat(),
                }
            )
            return res

    def list_snapshots(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._sf() as db:
            snaps = db.scalars(
                select(SnapshotManifestRow)
                .where(SnapshotManifestRow.workspace_id == workspace_id)
                .order_by(SnapshotManifestRow.created_at.desc())
            ).all()
            latest_semantics: dict[str, SemanticVersionRow] = {}
            for sem_row in db.scalars(
                select(SemanticVersionRow)
                .where(SemanticVersionRow.snapshot_id.in_([s.snapshot_id for s in snaps]))
                .order_by(SemanticVersionRow.version.desc())
            ):
                latest_semantics.setdefault(sem_row.snapshot_id, sem_row)
            res = []
            for s in snaps:
                item = dict(s.manifest_json)
                item.update(
                    {
                        "snapshot_id": s.snapshot_id,
                        "workspace_id": s.workspace_id,
                        "source_id": s.source_id,
                        "version": s.version,
                        "schema_fingerprint": s.schema_fingerprint,
                        "artifact_id": s.artifact_id,
                        "sha256": s.sha256,
                        "created_at": s.created_at.isoformat(),
                    }
                )
                sem = latest_semantics.get(s.snapshot_id)
                if sem:
                    item["semantic_version_id"] = sem.semantic_version_id
                    item["approved"] = sem.approved
                res.append(item)
            return res

    def delete_snapshot(self, workspace_id: str, snapshot_id: str) -> dict[str, Any] | None:
        with self._sf() as db:
            with db.begin():
                snap = db.get(SnapshotManifestRow, snapshot_id, with_for_update=True)
                if snap is None or snap.workspace_id != workspace_id:
                    return None
                removed = dict(snap.manifest_json)
                db.execute(delete(SemanticVersionRow).where(SemanticVersionRow.snapshot_id == snapshot_id))
                source = db.get(SourceRow, snap.source_id)
                if source is not None and source.current_snapshot_id == snapshot_id:
                    source.current_snapshot_id = None
                db.delete(snap)
                return removed

    def pinned_runs_for_snapshot(self, workspace_id: str, snapshot_id: str) -> list[str]:
        with self._sf() as db:
            return list(
                db.scalars(
                    select(PinRow.run_id)
                    .join(RunRow, RunRow.run_id == PinRow.run_id)
                    .where(PinRow.workspace_id == workspace_id, RunRow.snapshot_id == snapshot_id)
                )
            )

    def get_semantics(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._sf() as db:
            sem = db.scalars(
                select(SemanticVersionRow)
                .where(SemanticVersionRow.snapshot_id == snapshot_id)
                .order_by(SemanticVersionRow.version.desc())
            ).first()
            if not sem:
                return None
            res = dict(sem.definitions_json)
            res["semantic_version_id"] = sem.semantic_version_id
            res["snapshot_id"] = sem.snapshot_id
            res["version"] = sem.version
            res["approved"] = sem.approved
            return res

    def approve_semantics(self, snapshot_id: str, expected_version: int) -> dict[str, Any]:
        try:
            with self._sf() as db:
                with db.begin():
                    current = db.scalars(
                        select(SemanticVersionRow)
                        .where(SemanticVersionRow.snapshot_id == snapshot_id)
                        .order_by(SemanticVersionRow.version.desc())
                    ).first()
                    if not current:
                        raise NotFoundError("Semantic version not found")
                    if current.version != expected_version:
                        raise VersionConflictError("Semantic version conflict")
                    approved = SemanticVersionRow(
                        semantic_version_id=f"sem_{uuid.uuid4().hex[:10]}",
                        workspace_id=current.workspace_id,
                        snapshot_id=snapshot_id,
                        version=current.version + 1,
                        approved=True,
                        definitions_json={**current.definitions_json, "approved": True, "version": current.version + 1},
                    )
                    db.add(approved)
                    db.flush()
                    return {
                        **approved.definitions_json,
                        "semantic_version_id": approved.semantic_version_id,
                        "snapshot_id": snapshot_id,
                        "version": approved.version,
                        "approved": True,
                    }
        except IntegrityError as exc:
            raise VersionConflictError("Semantic version conflict") from exc


class PgPinRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def create_pin(
        self,
        pin_id: str,
        workspace_id: str,
        title: str,
        question_text: str,
        run_id: str,
        artifact_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with self._sf() as db:
                with db.begin():
                    ensure_workspace(db, workspace_id)
                    now = datetime.now(timezone.utc)
                    pin = PinRow(
                        pin_id=pin_id,
                        workspace_id=workspace_id,
                        title=title,
                        question_text=question_text,
                        run_id=run_id,
                        version=1,
                        artifact_json=artifact_json or {},
                        created_at=now,
                    )
                    db.add(pin)
                    db.flush()
                    return {
                        "pin_id": pin_id,
                        "workspace_id": workspace_id,
                        "title": title,
                        "question_text": question_text,
                        "run_id": run_id,
                        "version": 1,
                        "created_at": now.isoformat(),
                    }
        except IntegrityError:
            # uq_pins_ws_run: a concurrent request already pinned this run; return that pin.
            existing = next((p for p in self.list_pins(workspace_id) if p["run_id"] == run_id), None)
            if existing is None:
                raise
            return existing

    def list_pins(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._sf() as db:
            pins = db.scalars(
                select(PinRow)
                .where(PinRow.workspace_id == workspace_id)
                .order_by(PinRow.created_at.desc())
            ).all()
            return [
                {
                    "pin_id": p.pin_id,
                    "workspace_id": p.workspace_id,
                    "title": p.title,
                    "question_text": p.question_text,
                    "run_id": p.run_id,
                    "version": p.version,
                    "created_at": p.created_at.isoformat(),
                }
                for p in pins
            ]

    def delete_pin(self, workspace_id: str, pin_id: str) -> bool:
        with self._sf() as db:
            with db.begin():
                stmt = delete(PinRow).where(
                    PinRow.workspace_id == workspace_id, PinRow.pin_id == pin_id
                )
                res = db.execute(stmt)
                return res.rowcount > 0


class PgBudgetLedger:
    def __init__(self, session_factory: sessionmaker[Session], max_attempts_default: int = 24) -> None:
        self._sf = session_factory
        self._max_attempts_default = max_attempts_default

    def configure_run(
        self,
        run_id: str,
        *,
        max_attempts: int,
        max_tokens: int = 500_000,
        max_cost_usd: Decimal | None = None,
    ) -> None:
        # Mode limits are read from the durable run row in reserve(); per-call cost ceilings
        # are passed through RoutingService and enforced transactionally there.
        del run_id, max_attempts, max_tokens, max_cost_usd

    def reserve(
        self,
        run_id: str,
        operation_id: str,
        attempt_id: str,
        estimated_tokens: int,
        estimated_cost_usd: Decimal | None,
        cost_is_unknown: bool,
        hard_cost_ceiling_usd: Decimal | None,
    ) -> BudgetReservation:
        if hard_cost_ceiling_usd is not None and cost_is_unknown:
            raise BudgetExhaustedError("Hard monetary ceiling excludes models with unknown pricing")

        with self._sf() as db:
            with db.begin():
                run_row = db.get(RunRow, run_id, with_for_update=True)
                if not run_row:
                    raise NotFoundError(f"Run {run_id} not found")

                existing = db.scalars(
                    select(BudgetReservationRow).where(BudgetReservationRow.run_id == run_id)
                ).all()
                attempt_limit = (
                    QUICK_BUDGET_LIMITS.total_model_attempts
                    if run_row.mode == "quick"
                    else DEEP_BUDGET_LIMITS.total_model_attempts
                )
                if len(existing) + 1 > min(attempt_limit, self._max_attempts_default):
                    raise BudgetExhaustedError(f"Run {run_id} exceeded max model attempts")

                cur_cost = sum(
                    (r.actual_cost_usd if r.settled and r.actual_cost_usd is not None else (r.reserved_cost_usd or Decimal("0")))
                    for r in existing
                )
                add_cost = estimated_cost_usd or Decimal("0")
                if hard_cost_ceiling_usd is not None and cur_cost + add_cost > hard_cost_ceiling_usd:
                    raise BudgetExhaustedError("Monetary ceiling exceeded")

                res_id = f"res_{uuid.uuid4().hex[:12]}"
                row = BudgetReservationRow(
                    reservation_id=res_id,
                    run_id=run_id,
                    operation_id=operation_id,
                    attempt_id=attempt_id,
                    reserved_tokens=estimated_tokens,
                    reserved_cost_usd=estimated_cost_usd,
                    cost_is_unknown=cost_is_unknown,
                    settled=False,
                )
                db.add(row)
                return BudgetReservation(
                    reservation_id=res_id,
                    run_id=run_id,
                    operation_id=operation_id,
                    attempt_id=attempt_id,
                    reserved_tokens=estimated_tokens,
                    reserved_cost_usd=estimated_cost_usd,
                    cost_is_unknown=cost_is_unknown,
                    settled=False,
                )

    def settle(
        self,
        reservation_id: str,
        actual_tokens: int,
        actual_cost_usd: Decimal | None,
    ) -> BudgetReservation:
        with self._sf() as db:
            with db.begin():
                row = db.get(BudgetReservationRow, reservation_id, with_for_update=True)
                if not row:
                    raise NotFoundError(f"Reservation {reservation_id} not found")
                if not row.settled:
                    row.settled = True
                    row.actual_tokens = actual_tokens
                    row.actual_cost_usd = actual_cost_usd
                return BudgetReservation(
                    reservation_id=row.reservation_id,
                    run_id=row.run_id,
                    operation_id=row.operation_id,
                    attempt_id=row.attempt_id,
                    reserved_tokens=row.reserved_tokens,
                    reserved_cost_usd=row.reserved_cost_usd,
                    cost_is_unknown=row.cost_is_unknown,
                    settled=True,
                    actual_tokens=row.actual_tokens,
                    actual_cost_usd=row.actual_cost_usd,
                )

    def release_unresolved(self, reservation_id: str, reason: str) -> None:
        with self._sf() as db:
            with db.begin():
                row = db.get(BudgetReservationRow, reservation_id, with_for_update=True)
                if row and not row.settled:
                    row.unresolved_reason = reason[:500]

    def get_runs_totals(self, run_ids: list[str]) -> dict[str, dict[str, object]]:
        with self._sf() as db:
            rows = db.execute(
                select(
                    BudgetReservationRow.run_id,
                    func.count(),
                    func.sum(func.coalesce(BudgetReservationRow.actual_tokens, BudgetReservationRow.reserved_tokens)),
                    func.sum(func.coalesce(BudgetReservationRow.actual_cost_usd, BudgetReservationRow.reserved_cost_usd, 0)),
                    func.max(case((BudgetReservationRow.settled.is_(False), 1), else_=0)),
                )
                .where(BudgetReservationRow.run_id.in_(run_ids))
                .group_by(BudgetReservationRow.run_id)
            ).all()
        found = {
            run_id: {
                "run_id": run_id,
                "used_attempts": int(count),
                "total_tokens": int(tokens or 0),
                "total_cost_usd": str(cost or Decimal("0")),
                "incomplete": bool(incomplete),
            }
            for run_id, count, tokens, cost, incomplete in rows
        }
        return {
            run_id: found.get(run_id)
            or {"run_id": run_id, "used_attempts": 0, "total_tokens": 0, "total_cost_usd": "0", "incomplete": False}
            for run_id in run_ids
        }

    def get_run_totals(self, run_id: str) -> dict[str, object]:
        with self._sf() as db:
            rows = db.scalars(
                select(BudgetReservationRow).where(BudgetReservationRow.run_id == run_id)
            ).all()
        tokens = sum(
            row.actual_tokens if row.settled and row.actual_tokens is not None else row.reserved_tokens
            for row in rows
        )
        cost = sum(
            (
                row.actual_cost_usd
                if row.settled and row.actual_cost_usd is not None
                else row.reserved_cost_usd or Decimal("0")
            )
            for row in rows
        )
        return {
            "run_id": run_id,
            "used_attempts": len(rows),
            "total_tokens": tokens,
            "total_cost_usd": str(cost),
            "incomplete": any(not row.settled for row in rows),
        }
