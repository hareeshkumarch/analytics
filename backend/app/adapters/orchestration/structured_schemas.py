from typing import Any

_STRINGS: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_NULLABLE_STRING: dict[str, Any] = {"type": ["string", "null"]}


def _strict(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


PLAN_SCHEMA = _strict(
    {
        "needs_clarification": {"type": "boolean"},
        "clarification_question": _NULLABLE_STRING,
        "goal": {"type": "string"},
        "completion_criteria": _STRINGS,
        "tasks": {
            "type": "array",
            "items": _strict(
                {
                    "task_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["data_specialist", "reconciliation_specialist", "evidence_reviewer"]},
                    "objective": {"type": "string"},
                    "dependencies": _STRINGS,
                    "required_inputs": _STRINGS,
                    "acceptance_criteria": _STRINGS,
                }
            ),
        },
    }
)

REPLAN_SCHEMA = _strict(
    {
        "action": {"type": "string", "enum": ["remediate", "stop"]},
        "kind": _NULLABLE_STRING,
        "objective": _NULLABLE_STRING,
        "reason": _NULLABLE_STRING,
    }
)

REVIEW_SCHEMA = _strict(
    {
        "approved": {"type": "boolean"},
        "gaps": _STRINGS,
        "contradictions": _STRINGS,
        "summary": {"type": "string"},
    }
)
