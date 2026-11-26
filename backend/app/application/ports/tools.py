from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolExecution:
    execution_id: str
    run_id: str
    task_id: str
    tool: str
    tool_version: str
    args_fingerprint: str
    status: str
    error_code: str | None
    duration_ms: int
    evidence_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ToolExecutionLedger(Protocol):
    def record(self, execution: ToolExecution) -> None: ...
    def list_for_runs(self, run_ids: list[str]) -> list[ToolExecution]: ...


def summarize_tool_executions(executions: list[ToolExecution]) -> list[dict[str, Any]]:
    by_tool: dict[str, list[ToolExecution]] = {}
    for execution in executions:
        by_tool.setdefault(execution.tool, []).append(execution)
    summary = []
    for tool, rows in sorted(by_tool.items()):
        tasks = {(r.run_id, r.task_id) for r in rows}
        summary.append(
            {
                "tool": tool,
                "calls": len(rows),
                "failed_calls": sum(r.status in ("failed", "denied") for r in rows),
                "invalid_calls": sum(r.error_code == "INVALID_ARGUMENT" for r in rows),
                "duplicate_calls": sum(r.error_code == "DUPLICATE_FAILED_CALL" for r in rows),
                "timeouts": sum(r.error_code == "TIMEOUT" for r in rows),
                "avg_duration_ms": round(sum(r.duration_ms for r in rows) / len(rows)),
                "calls_per_task": round(len(rows) / len(tasks), 2),
            }
        )
    return summary
