from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.errors import BudgetExhaustedError, ValidationError
from app.domain.limits import DEEP_BUDGET_LIMITS, QUICK_BUDGET_LIMITS, RunBudgetLimits
from app.domain.models import Claim, PlanDAG, RunMode, TaskNode, TaskStatus


@dataclass(frozen=True, slots=True)
class TaskAttemptResult:
    task_id: str
    attempt: int
    generation: int
    result_hash: str
    payload: dict[str, Any]
    evidence_ids: tuple[str, ...] = ()


@dataclass
class SpecialistOutcome:
    attempt: TaskAttemptResult
    claim: Claim | None
    provider: str
    model: str
    error: str | None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    review_verdict: ReviewVerdict | None = None


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    approved: bool
    gaps: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()


def merge_task_results_reducer(
    existing: dict[str, TaskAttemptResult],
    incoming: TaskAttemptResult,
    *,
    active_generation: int,
) -> dict[str, TaskAttemptResult]:
    if incoming.generation < active_generation:
        raise ValidationError(f"Rejected stale generation {incoming.generation} < {active_generation}")
    key = f"{incoming.task_id}:{incoming.attempt}"
    merged = dict(existing)
    if key in merged:
        prev = merged[key]
        if prev.result_hash != incoming.result_hash:
            raise ValidationError(
                f"Conflicting result hash for {key}: {prev.result_hash} != {incoming.result_hash}"
            )
        return merged
    merged[key] = incoming
    return merged


@dataclass
class SchedulerState:
    run_id: str
    mode: RunMode
    limits: RunBudgetLimits
    generation: int = 1
    replan_count: int = 0
    accepted_task_count: int = 0
    specialist_instances_used: int = 0
    plan: PlanDAG | None = None
    task_statuses: dict[str, TaskStatus] = field(default_factory=dict)
    results: dict[str, TaskAttemptResult] = field(default_factory=dict)
    cancelled: bool = False


class DeterministicSupervisor:
    def __init__(self, mode: RunMode, run_id: str) -> None:
        limits = QUICK_BUDGET_LIMITS if mode == RunMode.QUICK else DEEP_BUDGET_LIMITS
        self.state = SchedulerState(run_id=run_id, mode=mode, limits=limits)

    def accept_plan(self, plan: PlanDAG, is_replan: bool = False) -> None:
        if self.state.cancelled:
            raise ValidationError("Cannot accept plan on cancelled run")
        plan.validate_dag()
        if is_replan:
            if self.state.replan_count >= self.state.limits.max_replans:
                raise BudgetExhaustedError(
                    f"Mode {self.state.mode.value} exceeded max_replans ({self.state.limits.max_replans})"
                )
            self.state.replan_count += 1
            self.state.generation += 1
            for tid, st in list(self.state.task_statuses.items()):
                if st in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    self.state.task_statuses[tid] = TaskStatus.SUPERSEDED

        new_tasks = [t for t in plan.tasks if t.task_id not in self.state.task_statuses]
        new_total_tasks = self.state.accepted_task_count + len(new_tasks)
        if new_total_tasks > self.state.limits.accepted_dag_tasks:
            raise BudgetExhaustedError(
                f"Plan exceeds accepted_dag_tasks limit ({new_total_tasks} > {self.state.limits.accepted_dag_tasks})"
            )

        self.state.plan = plan
        self.state.accepted_task_count = new_total_tasks
        for task in new_tasks:
            self.state.task_statuses[task.task_id] = TaskStatus.PENDING

    def resume_revision(self, revision: int) -> None:
        self.state.replan_count = max(0, revision - 1)
        self.state.generation = max(1, revision)

    def restore_status(self, task_id: str, status: TaskStatus) -> None:
        if task_id in self.state.task_statuses:
            self.state.task_statuses[task_id] = status

    def next_ready_tasks(self, max_tasks: int | None = None) -> tuple[TaskNode, ...]:
        if self.state.cancelled or self.state.plan is None:
            return ()
        self._propagate_failed_dependencies()
        running_count = sum(1 for s in self.state.task_statuses.values() if s == TaskStatus.RUNNING)
        available_slots = max(0, self.state.limits.concurrent_specialists - running_count)
        if max_tasks is not None:
            available_slots = min(available_slots, max(0, max_tasks))
        if available_slots == 0:
            return ()

        ready: list[TaskNode] = []
        for task in sorted(self.state.plan.tasks, key=lambda t: (-t.priority, t.task_id)):
            if self.state.task_statuses.get(task.task_id) != TaskStatus.PENDING:
                continue
            deps_done = all(
                self.state.task_statuses.get(dep) == TaskStatus.COMPLETED for dep in task.dependencies
            )
            if deps_done:
                if self.state.specialist_instances_used + len(ready) >= self.state.limits.lifetime_specialist_instances:
                    if not ready:
                        raise BudgetExhaustedError("Exceeded lifetime_specialist_instances")
                    break
                ready.append(task)
                if len(ready) >= available_slots:
                    break

        for t in ready:
            self.state.task_statuses[t.task_id] = TaskStatus.RUNNING
            self.state.specialist_instances_used += 1
        return tuple(ready)

    def remaining_specialist_capacity(self) -> int:
        return max(0, self.state.limits.lifetime_specialist_instances - self.state.specialist_instances_used)

    def can_accept_replan(self, task_count: int) -> bool:
        limits = self.state.limits
        return (
            not self.state.cancelled
            and self.state.replan_count < limits.max_replans
            and self.remaining_specialist_capacity() >= task_count
            and self.state.accepted_task_count + task_count <= limits.accepted_dag_tasks
        )

    def abandon_pending(self) -> tuple[str, ...]:
        abandoned = tuple(
            tid for tid, status in self.state.task_statuses.items() if status == TaskStatus.PENDING
        )
        for tid in abandoned:
            self.state.task_statuses[tid] = TaskStatus.CANCELLED
        return abandoned

    def record_task_result(self, result: TaskAttemptResult, *, failed: bool = False) -> None:
        self.state.results = merge_task_results_reducer(
            self.state.results, result, active_generation=self.state.generation
        )
        self.state.task_statuses[result.task_id] = TaskStatus.FAILED if failed else TaskStatus.COMPLETED

    def _propagate_failed_dependencies(self) -> None:
        if self.state.plan is None:
            return
        failed_dependencies = {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED}
        changed = True
        while changed:
            changed = False
            for task in self.state.plan.tasks:
                if self.state.task_statuses.get(task.task_id) != TaskStatus.PENDING:
                    continue
                if any(self.state.task_statuses.get(dep) in failed_dependencies for dep in task.dependencies):
                    self.state.task_statuses[task.task_id] = TaskStatus.FAILED
                    changed = True

    def all_tasks_joined(self) -> bool:
        if self.state.plan is None:
            return False
        self._propagate_failed_dependencies()
        terminal_statuses = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
        return all(
            self.state.task_statuses.get(t.task_id) in terminal_statuses
            for t in self.state.plan.tasks
        )

    def cancel(self) -> None:
        self.state.cancelled = True
        for tid, st in list(self.state.task_statuses.items()):
            if st in (TaskStatus.PENDING, TaskStatus.RUNNING):
                self.state.task_statuses[tid] = TaskStatus.CANCELLED
