from typing import Any, Generator

import httpx

from app.adapters.providers.http_utils import classify_http_error, parse_strict_json, read_anthropic_event, stream_sse_text
from app.adapters.providers.message_format import to_anthropic
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.domain.errors import ProviderError, ValidationError
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile


class AnthropicMessagesAdapter:
    provider_id = ProviderId.ANTHROPIC

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def build_payload(self, request: ModelRequest, model: ModelProfile) -> dict[str, Any]:
        if request.structured_schema and not model.capabilities.structured_output:
            raise ValidationError(f"Model {model.model_id} does not support structured_output")

        system_text, chat_msgs = to_anthropic(request.messages)

        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": chat_msgs,
            "max_tokens": min(request.max_output_tokens, model.max_output_tokens),
        }
        if model.capabilities.sampling_params:
            payload["temperature"] = request.temperature
        if model.capabilities.refusal_fallback:
            payload["fallbacks"] = "default"
        if system_text:
            payload["system"] = system_text
        if request.tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters_schema,
                    **({"strict": True} if t.strict else {}),
                }
                for t in request.tools
            ]
        if request.tools and request.tool_choice:
            payload["tool_choice"] = {"type": request.tool_choice}
        if request.structured_schema:
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request.structured_schema,
                }
            }
        return payload

    def stream(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> Generator[str, None, ModelResponse]:
        if not api_key:
            raise ProviderError("Anthropic API key not configured", code="setup_required", retryable=False)
        text, state = yield from stream_sse_text(
            f"{provider_profile.base_url.rstrip('/')}/v1/messages",
            {**self.build_payload(request, model_profile), "stream": True},
            {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            request.timeout_seconds,
            self._transport,
            "Anthropic",
            read_anthropic_event,
        )
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=text,
            finish_reason=str(state["finish_reason"]),
            input_tokens=int(state["input_tokens"]),
            output_tokens=int(state["output_tokens"]),
        )

    def invoke(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> ModelResponse:
        if not api_key:
            raise ProviderError("Anthropic API key not configured", code="setup_required", retryable=False)
        payload = self.build_payload(request, model_profile)
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if model_profile.capabilities.refusal_fallback:
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        with httpx.Client(timeout=request.timeout_seconds, transport=self._transport) as client:
            resp = client.post(f"{provider_profile.base_url.rstrip('/')}/v1/messages", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise classify_http_error(resp.status_code, resp.text, "Anthropic")

        data = resp.json()
        stop_reason = data.get("stop_reason", "end_turn")
        refusal = "Model refused request" if stop_reason == "refusal" else None

        text_parts: list[str] = []
        tool_calls: list[ToolCallResult] = []
        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCallResult(
                        call_id=block.get("id", ""),
                        tool_name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )

        full_text = "".join(text_parts)
        structured = (
            parse_strict_json(full_text, request.structured_schema)
            if request.structured_schema and not refusal and not tool_calls
            else None
        )
        usage = data.get("usage", {})
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=full_text,
            tool_calls=tuple(tool_calls),
            structured_output=structured,
            finish_reason=stop_reason,
            refusal_reason=refusal,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            continuation_state={"content": data.get("content", [])} if tool_calls else None,
        )
