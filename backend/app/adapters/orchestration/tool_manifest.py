
from app.domain.errors import SecurityPolicyError

ALLOWED_ANALYTICAL_TOOLS = frozenset({
    "run_query_ir",
    "inspect_schema",
    "calculate",
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
