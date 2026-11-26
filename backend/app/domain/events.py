from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    RUN_STATUS = "run.status"
    RUN_PROGRESS = "run.progress"
    PLAN_UPDATED = "plan.updated"
    TASK_STATUS = "task.status"
    ROUTE_SELECTED = "route.selected"
    TOOL_RESULT = "tool.result"
    MODEL_ATTEMPT = "model.attempt"
    CLARIFICATION_REQUIRED = "clarification.required"
    ARTIFACT_READY = "artifact.ready"
    ANSWER_DELTA = "answer.delta"
    ANSWER_RESET = "answer.reset"
    RUN_FINISHED = "run.finished"
    BUDGET_WARNING = "budget.warning"


@dataclass(frozen=True, slots=True)
class RunEvent:
    schema_version: int
    event_id: str
    sequence: int
    workspace_id: str
    run_id: str
    type: EventType
    occurred_at: datetime
    payload: dict[str, object] = field(default_factory=dict)

    def to_sse_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "workspace_id": self.workspace_id,
            "run_id": self.run_id,
            "type": self.type.value,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": dict(self.payload),
        }
