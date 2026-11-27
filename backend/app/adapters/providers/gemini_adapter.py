import copy
import uuid
from typing import Any

import httpx

from app.adapters.providers.http_utils import classify_http_error, parse_strict_json, read_json_body
from app.adapters.providers.message_format import parse_arguments, to_gemini_input
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.domain.errors import ProviderError, ValidationError
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile


def _step_text(step: dict[str, Any]) -> str:
    if step.get("type") == "text":
        return str(step.get("text", ""))
    content = step.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


class GeminiInteractionsAdapter:
    provider_id = ProviderId.GEMINI

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def build_payload(self, request: ModelRequest, model: ModelProfile) -> dict[str, Any]:
        if request.structured_schema and not model.capabilities.structured_output:
            raise ValidationError(f"Model {model.model_id} does not support structured_output")

        system_text, steps = to_gemini_input(request.messages)
        generation_config: dict[str, Any] = {
            "max_output_tokens": min(request.max_output_tokens, model.max_output_tokens),
        }
        payload: dict[str, Any] = {
            "model": model.model_id,
            "input": steps,
            "store": False,
            "generation_config": generation_config,
        }
        if system_text:
            payload["system_instruction"] = system_text
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema,
                }
                for t in request.tools
            ]
            if request.tool_choice:
                generation_config["tool_choice"] = request.tool_choice
        if request.structured_schema:
            payload["response_format"] = request.structured_schema
            payload["response_mime_type"] = "application/json"
        return payload

    def invoke(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> ModelResponse:
        if not api_key:
            raise ProviderError("Gemini API key not configured", code="setup_required", retryable=False)
        payload = self.build_payload(request, model_profile)
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }
        url = f"{provider_profile.base_url.rstrip('/')}/v1beta/interactions"
        with httpx.Client(timeout=request.timeout_seconds, transport=self._transport) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise classify_http_error(resp.status_code, resp.text, "Gemini", resp.headers.get("retry-after"))

        data = read_json_body(resp, "Gemini")
        text_parts: list[str] = []
        tool_calls: list[ToolCallResult] = []
        steps = copy.deepcopy(data.get("steps") or data.get("outputs") or [])
        for step in steps:
            stype = step.get("type")
            if stype == "function_call":
                if not step.get("id"):
                    step["id"] = f"call_{uuid.uuid4().hex[:12]}"
                tool_calls.append(
                    ToolCallResult(
                        call_id=step["id"],
                        tool_name=step.get("name", ""),
                        arguments=parse_arguments(step.get("arguments")),
                    )
                )
            elif stype in ("text", "model_output"):
                text_parts.append(_step_text(step))

        full_text = "".join(text_parts)
        structured = (
            parse_strict_json(full_text, request.structured_schema)
            if request.structured_schema and not tool_calls
            else None
        )
        usage = data.get("usage", {})
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=full_text,
            tool_calls=tuple(tool_calls),
            structured_output=structured,
            finish_reason=data.get("status", "completed"),
            input_tokens=int(usage.get("total_input_tokens", 0)),
            cached_input_tokens=int(usage.get("total_cached_tokens") or 0),
            output_tokens=int(usage.get("total_output_tokens", 0)),
            reasoning_tokens=int(usage.get("total_thought_tokens", 0)),
            continuation_state={"steps": steps} if tool_calls else None,
        )
