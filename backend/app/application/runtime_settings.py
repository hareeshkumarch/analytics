import os
from enum import Enum


class ExecutionMode(str, Enum):
    INLINE = "inline"
    CELERY = "celery"


class AgentRuntime(str, Enum):
    DEEP_AGENTS = "deepagents"
    LEGACY = "legacy"


def _choice(name: str, kind: type[ExecutionMode] | type[AgentRuntime], default: str) -> str:
    raw = os.environ.get(name, default).strip().lower() or default
    allowed = [member.value for member in kind]
    if raw not in allowed:
        raise RuntimeError(f"{name}={raw!r} is not valid; use one of {', '.join(allowed)}.")
    return raw


def execution_mode() -> ExecutionMode:
    return ExecutionMode(_choice("RUN_EXECUTION_MODE", ExecutionMode, ExecutionMode.INLINE.value))


def uses_celery() -> bool:
    return execution_mode() == ExecutionMode.CELERY


def agent_runtime() -> AgentRuntime:
    return AgentRuntime(_choice("AGENT_RUNTIME", AgentRuntime, AgentRuntime.DEEP_AGENTS.value))


def follow_up_suggestions() -> bool:
    """Whether to spend one small model call after each answer on suggested next questions. On unless FOLLOW_UP_SUGGESTIONS=0."""
    return os.environ.get("FOLLOW_UP_SUGGESTIONS", "1").strip().lower() not in ("0", "false", "off", "no")
