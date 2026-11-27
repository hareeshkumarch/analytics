from typing import Any, Generator

import httpx

from app.adapters.providers.http_utils import classify_http_error, parse_strict_json, read_chat_completions_event, stream_sse_text, raise_if_truncated, read_json_body
from app.adapters.providers.message_format import parse_arguments, to_chat_completions
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.domain.errors import ProviderError, ValidationError
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile


class NvidiaNimAdapter:
    provider_id = ProviderId.NVIDIA_NIM

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def build_payload(self, request: ModelRequest, model: ModelProfile) -> dict[str, Any]:
        if request.structured_schema and request.tools and not model.capabilities.structured_with_tools:
            raise ValidationError(
                f"NVIDIA NIM model {model.model_id} does not support combining structured_output with tools"
            )
        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": to_chat_completions(request.messages, "nvidia_nim"),
            "max_tokens": min(request.max_output_tokens, model.max_output_tokens),
            "temperature": request.temperature,
            "stream": False,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters_schema,
                    },
                }
                for t in request.tools
            ]
        if request.tools and request.tool_choice:
            payload["tool_choice"] = request.tool_choice
        if request.structured_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "schema": request.structured_schema,
                },
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
            raise ProviderError("NVIDIA NIM API key not configured", code="setup_required", retryable=False)
        text, state = yield from stream_sse_text(
            f"{provider_profile.base_url.rstrip('/')}/chat/completions",
            {**self.build_payload(request, model_profile), "stream": True, "stream_options": {"include_usage": True}},
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            request.timeout_seconds,
            self._transport,
            "NVIDIA NIM",
            read_chat_completions_event,
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
            raise ProviderError("NVIDIA NIM API key not configured", code="setup_required", retryable=False)
        payload = self.build_payload(request, model_profile)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=request.timeout_seconds, transport=self._transport) as client:
            resp = client.post(
                f"{provider_profile.base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
            )
        if resp.status_code >= 400:
            raise classify_http_error(resp.status_code, resp.text, "NVIDIA NIM", resp.headers.get("retry-after"))

        data = read_json_body(resp, "NVIDIA NIM")
        choice = (data.get("choices") or [{}])[0]
        raise_if_truncated(choice.get("finish_reason"), "NVIDIA NIM")
        msg = choice.get("message", {})
        text = msg.get("content") or ""
        tool_calls = tuple(
            ToolCallResult(
                call_id=tc.get("id", ""),
                tool_name=str((tc.get("function") or {}).get("name") or ""),
                arguments=parse_arguments((tc.get("function") or {}).get("arguments")),
            )
            for tc in (msg.get("tool_calls") or [])
        )
        structured = (
            parse_strict_json(text, request.structured_schema)
            if request.structured_schema and not tool_calls
            else None
        )
        usage = data.get("usage", {})
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=text,
            tool_calls=tool_calls,
            structured_output=structured,
            finish_reason=choice.get("finish_reason", "stop"),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            cached_input_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )
