import hashlib
import json
from dataclasses import asdict
from typing import Any

from app.adapters.orchestration.supervisor import ReviewVerdict, SpecialistOutcome, TaskAttemptResult
from app.application.ports.tasks import TaskStore
from app.domain.models import Claim, PlanDAG, TaskBudget, TaskNode, TaskRecord, TaskStatus

MIN_TOOL_CALLS_PER_TASK = 2


def task_fingerprints(plan: PlanDAG, question: str) -> dict[str, str]:
    by_id = {task.task_id: task for task in plan.tasks}
    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
    found: dict[str, str] = {}

    def fingerprint(task: TaskNode) -> str:
        if task.task_id not in found:
            body = {
                "kind": task.kind,
                "objective": task.objective,
                "criteria": list(task.acceptance_criteria),
                "snapshot_id": task.snapshot_id,
                "semantic_version_id": task.semantic_version_id,
                "question": question_hash,
                "inputs": {dep: fingerprint(by_id[dep]) for dep in sorted(task.dependencies)},
            }
            found[task.task_id] = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        return found[task.task_id]

    for task in plan.tasks:
        fingerprint(task)
    return found


def budget_slice(*, tokens_left: int, reserve_tokens: int, tools_left: int, unfinished: int, min_tokens: int) -> TaskBudget:
    share = max(1, unfinished)
    return TaskBudget(
        tokens=max(min_tokens, (tokens_left - reserve_tokens) // share),
        tool_calls=max(min(MIN_TOOL_CALLS_PER_TASK, tools_left), tools_left // share),
    )


def outcome_result(outcome: SpecialistOutcome) -> dict[str, Any]:
    verdict = outcome.review_verdict
    return {
        "text": str(outcome.attempt.payload.get("text") or ""),
        "result_hash": outcome.attempt.result_hash,
        "claim": {**asdict(outcome.claim), "value": None if outcome.claim.value is None else str(outcome.claim.value)} if outcome.claim else None,
        "review_verdict": {"approved": verdict.approved, "gaps": list(verdict.gaps), "contradictions": list(verdict.contradictions)}
        if verdict is not None
        else None,
        "provider": outcome.provider,
        "model": outcome.model,
        "tool_calls": len(outcome.tool_trace),
    }


def restored_attempt(record: TaskRecord, generation: int) -> TaskAttemptResult:
    result = record.result or {}
    return TaskAttemptResult(
        task_id=record.task_id,
        attempt=max(1, record.attempt),
        generation=generation,
        result_hash=str(result.get("result_hash") or record.input_fingerprint),
        payload={"text": str(result.get("text") or ""), "restored": True},
        evidence_ids=record.evidence_ids,
    )


def restored_claim(record: TaskRecord) -> Claim | None:
    raw = (record.result or {}).get("claim")
    if not isinstance(raw, dict):
        return None
    return Claim(
        claim_id=str(raw.get("claim_id") or f"clm_{record.task_id}"),
        label=str(raw.get("label") or "observation"),
        statement=str(raw.get("statement") or ""),
        value=raw.get("value"),
        unit=raw.get("unit"),
        currency=raw.get("currency"),
        evidence_ids=tuple(str(e) for e in raw.get("evidence_ids") or ()),
        cell_refs=tuple(str(c) for c in raw.get("cell_refs") or ()),
    )


def restored_verdict(record: TaskRecord) -> ReviewVerdict | None:
    raw = (record.result or {}).get("review_verdict")
    if not isinstance(raw, dict):
        return None
    return ReviewVerdict(
        approved=bool(raw.get("approved")),
        gaps=tuple(str(g) for g in raw.get("gaps") or ()),
        contradictions=tuple(str(c) for c in raw.get("contradictions") or ()),
    )


class TaskLedger:
    def __init__(self, store: TaskStore, run_id: str) -> None:
        self._store = store
        self._run_id = run_id
        self.records: dict[str, TaskRecord] = {r.task_id: r for r in store.list_for_run(run_id)}

    def add_plan(self, plan: PlanDAG, fingerprints: dict[str, str]) -> None:
        new = [
            TaskRecord(
                run_id=self._run_id,
                task_id=task.task_id,
                plan_revision=plan.revision,
                kind=task.kind,
                objective=task.objective,
                acceptance_criteria=task.acceptance_criteria,
                dependencies=task.dependencies,
                status=TaskStatus.PENDING.value,
                attempt=0,
                input_fingerprint=fingerprints[task.task_id],
                version=1,
            )
            for task in plan.tasks
            if task.task_id not in self.records
        ]
        self._store.add_tasks(new)
        self.records.update({r.task_id: r for r in new})

    def move(self, task_id: str, status: TaskStatus, **fields: Any) -> TaskRecord:
        current = self.records[task_id]
        updated = self._store.transition(self._run_id, task_id, expected_version=current.version, status=status.value, **fields)
        self.records[task_id] = updated
        return updated

    def sync_statuses(self, statuses: dict[str, TaskStatus]) -> None:
        for task_id, status in statuses.items():
            record = self.records.get(task_id)
            if record is None or record.status == status.value or status in (TaskStatus.RUNNING, TaskStatus.COMPLETED):
                continue
            if record.status in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
                self.move(task_id, status)
