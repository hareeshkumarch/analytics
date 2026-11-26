from typing import Any, Protocol

from app.domain.models import TaskBudget, TaskRecord


class TaskStore(Protocol):
    def list_for_run(self, run_id: str) -> list[TaskRecord]: ...

    def add_tasks(self, records: list[TaskRecord]) -> None: ...

    def transition(
        self,
        run_id: str,
        task_id: str,
        *,
        expected_version: int,
        status: str,
        attempt: int | None = None,
        input_fingerprint: str | None = None,
        evidence_ids: tuple[str, ...] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        budget: TaskBudget | None = None,
    ) -> TaskRecord: ...
