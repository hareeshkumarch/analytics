from decimal import Decimal
from typing import Any

import httpx

from app.adapters.providers.http_utils import classify_http_error, parse_strict_json
from app.adapters.providers.message_format import parse_arguments, to_responses_input
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.domain.errors import ProviderError, ValidationError
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile


class XaiResponsesAdapter:
    provider_id = ProviderId.XAI

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def build_payload(self, request: ModelRequest, model: ModelProfile) -> dict[str, Any]:
        if request.structured_schema and not model.capabilities.structured_output:
            raise ValidationError(f"xAI model {model.model_id} does not support structured_output")
        payload: dict[str, Any] = {
            "model": model.model_id,
            "input": to_responses_input(request.messages, self.provider_id.value),
            "max_output_tokens": min(request.max_output_tokens, model.max_output_tokens),
            "store": False,
        }
        if model.capabilities.sampling_params:
            payload["temperature"] = request.temperature
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
        if request.tools and request.tool_choice:
            payload["tool_choice"] = request.tool_choice
        if request.tools and not model.capabilities.sampling_params:
            payload["include"] = ["reasoning.encrypted_content"]
        if request.structured_schema:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "structured_response",
                    "schema": request.structured_schema,
                    "strict": True,
                }
            }
        return payload

    def invoke(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> ModelResponse:
        if not api_key:
            raise ProviderError("xAI API key not configured", code="setup_required", retryable=False)
        payload = self.build_payload(request, model_profile)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=request.timeout_seconds, transport=self._transport) as client:
            resp = client.post(f"{provider_profile.base_url.rstrip('/')}/responses", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise classify_http_error(resp.status_code, resp.text, "xAI")

        data = resp.json()
        text_parts: list[str] = []
        tool_calls: list[ToolCallResult] = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        text_parts.append(c.get("text", ""))
            elif item.get("type") == "function_call":
                args_raw = item.get("arguments", "{}")
                args = parse_arguments(args_raw)
                tool_calls.append(
                    ToolCallResult(
                        call_id=item.get("call_id", item.get("id", "")),
                        tool_name=item.get("name", ""),
                        arguments=args,
                    )
                )

        full_text = "".join(text_parts)
        structured = (
            parse_strict_json(full_text, request.structured_schema)
            if request.structured_schema and not tool_calls
            else None
        )
        usage = data.get("usage", {})
        ticks = usage.get("cost_in_usd_ticks")
        reported_cost = (Decimal(str(ticks)) / Decimal("10000000000")) if ticks is not None else None
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=full_text,
            tool_calls=tuple(tool_calls),
            structured_output=structured,
            finish_reason=data.get("status", "completed"),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            continuation_state={"output": data.get("output", [])} if tool_calls else None,
            reported_cost_usd=reported_cost,
        )
