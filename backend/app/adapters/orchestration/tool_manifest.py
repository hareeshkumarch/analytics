import json
from typing import Any

from app.domain.errors import SecurityPolicyError
from app.domain.limits import DEFAULT_GLOBAL_LIMITS

ALLOWED_ANALYTICAL_TOOLS = frozenset({
    "run_query_ir",
    "inspect_schema",
    "read_staged_artifact_slice",
    "record_evidence",
})

FORBIDDEN_DEFAULT_TOOLS = frozenset({
    "task",
    "execute",
    "bash",
    "shell",
    "python",
    "interpreter",
    "browser",
    "web_search",
    "fetch_url",
    "write_file",
    "edit_file",
    "delete_file",
    "mcp_discover",
    "general_purpose",
})


def verify_restricted_tool_manifest(tool_names: list[str] | tuple[str, ...]) -> None:
    for name in tool_names:
        lower = name.lower()
        if lower in FORBIDDEN_DEFAULT_TOOLS or lower not in ALLOWED_ANALYTICAL_TOOLS:
            raise SecurityPolicyError(
                f"Unsafe or unapproved tool {name!r} detected in Deep Agents tool manifest; startup aborted."
            )


def cap_tool_result_for_model(result_obj: Any, artifact_id: str | None = None) -> str:
    raw = json.dumps(result_obj, default=str)
    limit = DEFAULT_GLOBAL_LIMITS.max_model_tool_result_bytes
    if len(raw.encode("utf-8")) <= limit:
        return raw
    summary = {
        "truncated": True,
        "max_bytes": limit,
        "artifact_id": artifact_id,
        "preview": raw[:2048],
        "note": "Full result stored as artifact; inspect via read_staged_artifact_slice.",
    }
    return json.dumps(summary)
