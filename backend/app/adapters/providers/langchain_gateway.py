"""LangChain chat-model adapter for the application's existing model gateway."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
import json
import threading
import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field, PrivateAttr

from app.application.ports.providers import ModelRequest, ModelResponse, ToolDefinition
from app.application.services.routing_service import RoutingService
from app.domain.providers import AgentRole, RoutingPolicy

_FINAL_TOOL_ROUND_PROMPT = (
    "The specialist tool-round limit is used up. Give your final answer now using only the results "
    "already available, and do not call any more tools."
)


def _message_to_gateway(message: BaseMessage) -> dict[str, Any]:
    role_map = {
        "system": "system",
        "developer": "system",
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "function": "tool",
    }
    role = role_map.get(message.type, getattr(message, "role", "user"))
    result: dict[str, Any] = {"role": role, "content": message.content}

    if isinstance(message, AIMessage):
        calls = []
        for call in message.tool_calls:
            calls.append(
                {
                    "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    "name": call["name"],
                    "arguments": call.get("args") or {},
                }
            )
        if calls:
            result["tool_calls"] = calls
        provider_state = message.additional_kwargs.get("provider_state")
        if isinstance(provider_state, dict):
            result["provider_state"] = provider_state

    if role == "tool":
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            result["tool_call_id"] = tool_call_id
        name = getattr(message, "name", None)
        if name:
            result["name"] = name
        if getattr(message, "status", None) == "error":
            result["is_error"] = True
    return result


def _tool_definition(tool: BaseTool | dict[str, Any] | type[Any] | Callable[..., Any]) -> ToolDefinition:
    converted = convert_to_openai_tool(tool)
    function = converted.get("function", converted)
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("LangChain tool is missing a name")
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    description = str(function.get("description") or "")
    return ToolDefinition(
        name=name,
        description=description,
        parameters_schema=parameters,
        strict=bool(function.get("strict", False)),
    )


class GatewayChatModel(BaseChatModel):
    """Expose RoutingService as a LangChain model without bypassing its policies."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    routing_service: RoutingService = Field(exclude=True)
    policy: RoutingPolicy
    run_id: str
    role: AgentRole
    model_name: str = "analytics-gateway"
    max_output_tokens: int = 12_000
    timeout_seconds: int = 120
    max_tool_rounds: int = 6
    should_cancel: Callable[[], bool] | None = Field(default=None, exclude=True)
    timeout_provider: Callable[[], int] | None = Field(default=None, exclude=True)
    response_observer: Callable[[ModelResponse], None] | None = Field(default=None, exclude=True)
    _tool_rounds: int = PrivateAttr(default=0)
    _tool_round_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "analytics_gateway"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name, "role": self.role.value}

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        del stop, kwargs
        return {
            "ls_provider": "analytics_gateway",
            "ls_model_name": self.model_name,
            "ls_model_type": "chat",
        }

    def bind_tools(
        self,
        tools: Sequence[BaseTool | dict[str, Any] | type[Any] | Callable[..., Any]],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        definitions = tuple(_tool_definition(tool) for tool in tools)
        return self.bind(tools=definitions, tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        if self.should_cancel is not None and self.should_cancel():
            raise RuntimeError("The investigation run was cancelled")

        raw_tools = kwargs.get("tools", ())
        tools = tuple(
            item if isinstance(item, ToolDefinition) else _tool_definition(item)
            for item in raw_tools
        )
        tool_choice = kwargs.get("tool_choice")
        structured_schema = kwargs.get("structured_schema")
        timeout = self.timeout_provider() if self.timeout_provider else self.timeout_seconds
        tool_rounds_exhausted = False
        if tools:
            with self._tool_round_lock:
                tool_rounds_exhausted = self._tool_rounds >= self.max_tool_rounds
        gateway_messages = [_message_to_gateway(message) for message in messages]
        if tool_rounds_exhausted:
            gateway_messages.append({"role": "user", "content": _FINAL_TOOL_ROUND_PROMPT})

        request = ModelRequest(
            request_id=f"lc_{uuid.uuid4().hex[:12]}",
            run_id=self.run_id,
            role=self.role,
            model_id="",
            messages=tuple(gateway_messages),
            tools=tools,
            structured_schema=structured_schema if isinstance(structured_schema, dict) else None,
            max_output_tokens=int(kwargs.get("max_output_tokens", self.max_output_tokens)),
            timeout_seconds=max(1, int(timeout)),
            tool_choice=(
                "none"
                if tool_rounds_exhausted
                else tool_choice if isinstance(tool_choice, str) else None
            ),
        )
        response = self.routing_service.execute_step(
            request=request,
            policy=self.policy,
            estimated_input_tokens=max(
                256,
                len(
                    json.dumps(
                        {
                            "messages": request.messages,
                            "tools": [
                                {
                                    "name": tool.name,
                                    "description": tool.description,
                                    "schema": tool.parameters_schema,
                                }
                                for tool in tools
                            ],
                        },
                        default=str,
                    )
                )
                // 3,
            ),
        )
        if response.tool_calls and tools:
            with self._tool_round_lock:
                if tool_rounds_exhausted:
                    # Some providers may ignore tool_choice="none". Keep the round cap
                    # authoritative and leave any proposed extra call unexecuted.
                    response = replace(response, tool_calls=())
                else:
                    self._tool_rounds += 1
        if self.response_observer is not None:
            self.response_observer(response)

        tool_calls = [
            {
                "name": call.tool_name,
                "args": call.arguments,
                "id": call.call_id or f"call_{uuid.uuid4().hex[:12]}",
            }
            for call in response.tool_calls
        ]
        additional_kwargs: dict[str, Any] = {}
        if response.continuation_state is not None:
            additional_kwargs["provider_state"] = {
                "provider": response.provider_id.value,
                "continuation": response.continuation_state,
            }
        if response.refusal_reason:
            additional_kwargs["refusal"] = response.refusal_reason

        message = AIMessage(
            content=response.text,
            tool_calls=tool_calls,
            additional_kwargs=additional_kwargs,
            response_metadata={
                "provider": response.provider_id.value,
                "model_name": response.model_id,
                "finish_reason": response.finish_reason,
            },
            usage_metadata={
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "total_tokens": response.input_tokens + response.output_tokens,
            },
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"provider": response.provider_id.value, "model_name": response.model_id},
        )
