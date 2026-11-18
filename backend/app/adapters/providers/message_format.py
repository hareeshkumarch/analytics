import json
import re
from typing import Any

Message = dict[str, Any]


def has_tool_turns(messages: tuple[Message, ...] | list[Message]) -> bool:
    return any(m.get("role") == "tool" or m.get("tool_calls") for m in messages)


def _continuation(message: Message, provider: str) -> dict[str, Any] | None:
    state = message.get("provider_state") or {}
    if state.get("provider") != provider:
        return None
    continuation = state.get("continuation")
    return continuation if isinstance(continuation, dict) else None


def _plain(message: Message) -> Message:
    return {"role": message.get("role", "user"), "content": message.get("content", "")}


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"_invalid_arguments": str(raw)[:2000]}
    return parsed if isinstance(parsed, dict) else {"_invalid_arguments": str(raw)[:2000]}


def _arguments_json(arguments: Any) -> str:
    return arguments if isinstance(arguments, str) else json.dumps(arguments, default=str)


def to_chat_completions(messages: tuple[Message, ...] | list[Message], provider: str) -> list[Message]:
    if not has_tool_turns(messages):
        return list(messages)
    out: list[Message] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            entry: Message = {
                "role": "assistant",
                "content": m.get("content") or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": _arguments_json(c.get("arguments", {}))},
                    }
                    for c in m["tool_calls"]
                ],
            }
            continuation = _continuation(m, provider)
            if continuation and continuation.get("reasoning_content"):
                entry["reasoning_content"] = continuation["reasoning_content"]
            out.append(entry)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m.get("content", "")})
        else:
            out.append(_plain(m))
    return out


def _replayable_output(continuation: dict[str, Any] | None) -> list[Message] | None:
    native = continuation.get("output") if continuation else None
    if not isinstance(native, list) or not native:
        return None
    return [
        item
        for item in native
        if isinstance(item, dict) and (item.get("type") != "reasoning" or item.get("encrypted_content"))
    ]


def to_responses_input(messages: tuple[Message, ...] | list[Message], provider: str | None = None) -> list[Message]:
    if not has_tool_turns(messages):
        return list(messages)
    out: list[Message] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            native = _replayable_output(_continuation(m, provider)) if provider else None
            if native:
                out.extend(native)
                continue
            if m.get("content"):
                out.append({"role": "assistant", "content": m["content"]})
            for c in m["tool_calls"]:
                out.append(
                    {
                        "type": "function_call",
                        "call_id": c["id"],
                        "name": c["name"],
                        "arguments": _arguments_json(c.get("arguments", {})),
                    }
                )
        elif role == "tool":
            out.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m.get("content", "")})
        else:
            out.append(_plain(m))
    return out


def _anthropic_id(call_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", str(call_id))
    return cleaned or "call"


def to_anthropic(messages: tuple[Message, ...] | list[Message]) -> tuple[str | None, list[Message]]:
    system_parts = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
    out: list[Message] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "assistant" and m.get("tool_calls"):
            continuation = _continuation(m, "anthropic")
            native = continuation.get("content") if continuation else None
            if isinstance(native, list) and native:
                content = native
            else:
                content = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                content += [
                    {"type": "tool_use", "id": _anthropic_id(c["id"]), "name": c["name"], "input": c.get("arguments", {})}
                    for c in m["tool_calls"]
                ]
            out.append({"role": "assistant", "content": content})
        elif role == "tool":
            block: Message = {"type": "tool_result", "tool_use_id": _anthropic_id(m["tool_call_id"]), "content": m.get("content", "")}
            if m.get("is_error"):
                block["is_error"] = True
            previous = out[-1] if out else None
            if (
                previous is not None
                and previous["role"] == "user"
                and isinstance(previous["content"], list)
                and all(b.get("type") == "tool_result" for b in previous["content"])
            ):
                previous["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            out.append(_plain(m))
    return ("\n".join(system_parts) if system_parts else None), out


def _gemini_user(text: str) -> Message:
    return {"type": "user_input", "content": [{"type": "text", "text": text}]}


def to_gemini_input(messages: tuple[Message, ...] | list[Message]) -> tuple[str | None, list[Message]]:
    system_parts = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
    out: list[Message] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "user":
            out.append(_gemini_user(str(m.get("content", ""))))
        elif role == "assistant":
            continuation = _continuation(m, "gemini")
            native = continuation.get("steps") if continuation else None
            if isinstance(native, list) and native:
                out.extend(native)
                continue
            if m.get("content"):
                out.append({"type": "model_output", "content": [{"type": "text", "text": str(m["content"])}]})
            for c in m.get("tool_calls") or []:
                out.append({"type": "function_call", "id": c["id"], "name": c["name"], "arguments": c.get("arguments", {})})
        elif role == "tool":
            result_text = str(m.get("content", ""))
            if m.get("is_error"):
                result_text = f"ERROR: {result_text}"
            out.append(
                {
                    "type": "function_result",
                    "name": m.get("name", ""),
                    "call_id": m["tool_call_id"],
                    "result": [{"type": "text", "text": result_text}],
                }
            )
    return ("\n".join(system_parts) if system_parts else None), out