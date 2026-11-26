import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.domain.errors import ValidationError, VersionConflictError
from app.domain.models import TaskBudget, TaskRecord, TaskStatus

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    TaskStatus.PENDING.value: frozenset(
        {TaskStatus.RUNNING.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value, TaskStatus.SUPERSEDED.value}
    ),
    TaskStatus.COMPLETED.value: frozenset({TaskStatus.RUNNING.value, TaskStatus.SUPERSEDED.value}),
    TaskStatus.RUNNING.value: frozenset(
        {
            TaskStatus.RUNNING.value,
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.SUPERSEDED.value,
        }
    ),
}


def check_transition(task_id: str, current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise ValidationError(f"Task {task_id} cannot move from {current} to {target}")


def apply_transition(
    record: TaskRecord,
    *,
    expected_version: int,
    status: str,
    attempt: int | None = None,
    input_fingerprint: str | None = None,
    evidence_ids: tuple[str, ...] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    budget: TaskBudget | None = None,
) -> TaskRecord:
    if record.version != expected_version:
        raise VersionConflictError(f"Task {record.task_id} is at version {record.version}, not {expected_version}")
    check_transition(record.task_id, record.status, status)
    return replace(
        record,
        status=status,
        version=record.version + 1,
        attempt=record.attempt if attempt is None else attempt,
        input_fingerprint=record.input_fingerprint if input_fingerprint is None else input_fingerprint,
        evidence_ids=record.evidence_ids if evidence_ids is None else evidence_ids,
        result=record.result if result is None else result,
        error=error,
        budget=record.budget if budget is None else budget,
        updated_at=datetime.now(timezone.utc),
    )


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], TaskRecord] = {}
        self._lock = threading.Lock()

    def list_for_run(self, run_id: str) -> list[TaskRecord]:
        with self._lock:
            return [r for (rid, _), r in self._records.items() if rid == run_id]

    def add_tasks(self, records: list[TaskRecord]) -> None:
        with self._lock:
            for record in records:
                self._records.setdefault((record.run_id, record.task_id), record)

    def transition(self, run_id: str, task_id: str, *, expected_version: int, status: str, **fields: Any) -> TaskRecord:
        with self._lock:
            current = self._records.get((run_id, task_id))
            if current is None:
                raise ValidationError(f"Task {task_id} is not recorded for run {run_id}")
            updated = apply_transition(current, expected_version=expected_version, status=status, **fields)
            self._records[(run_id, task_id)] = updated
            return updated
