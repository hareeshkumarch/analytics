from typing import Any, Generator

import httpx

from app.adapters.providers.http_utils import classify_http_error, parse_strict_json, read_responses_event, stream_sse_text, read_json_body
from app.adapters.providers.message_format import parse_arguments, to_responses_input
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.domain.errors import ProviderError, ValidationError
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile


class OpenAiResponsesAdapter:
    provider_id = ProviderId.OPENAI

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def build_payload(self, request: ModelRequest, model: ModelProfile) -> dict[str, Any]:
        if request.structured_schema and not model.capabilities.structured_output:
            raise ValidationError(f"Model {model.model_id} does not support structured_output")
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
                    "strict": t.strict,
                }
                for t in request.tools
            ]
        if request.tools and request.tool_choice:
            payload["tool_choice"] = request.tool_choice
        if request.tools and not model.capabilities.sampling_params:
            payload["include"] = ["reasoning.encrypted_content"]
        if model.reasoning_effort:
            payload["reasoning"] = {"effort": model.reasoning_effort}
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

    def stream(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> Generator[str, None, ModelResponse]:
        if not api_key:
            raise ProviderError("OpenAI API key not configured", code="setup_required", retryable=False)
        text, state = yield from stream_sse_text(
            f"{provider_profile.base_url.rstrip('/')}/responses",
            {**self.build_payload(request, model_profile), "stream": True},
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            request.timeout_seconds,
            self._transport,
            "OpenAI",
            read_responses_event,
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
            raise ProviderError("OpenAI API key not configured", code="setup_required", retryable=False)
        payload = self.build_payload(request, model_profile)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=request.timeout_seconds, transport=self._transport) as client:
            resp = client.post(f"{provider_profile.base_url.rstrip('/')}/responses", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise classify_http_error(resp.status_code, resp.text, "OpenAI", resp.headers.get("retry-after"))

        data = read_json_body(resp, "OpenAI")
        text_parts: list[str] = []
        tool_calls: list[ToolCallResult] = []
        refusal: str | None = None

        for item in data.get("output", []):
            itype = item.get("type")
            if itype == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        text_parts.append(c.get("text", ""))
                    elif c.get("type") == "refusal":
                        refusal = c.get("refusal", "Model refused request")
            elif itype == "function_call":
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
            if request.structured_schema and not refusal and not tool_calls
            else None
        )
        usage = data.get("usage", {})
        # OpenAI's output_tokens already includes the reasoning tokens. Report them apart, so the
        # router's input + output + reasoning adds up to what was billed instead of counting them twice.
        reasoning_tokens = int((usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=full_text,
            tool_calls=tuple(tool_calls),
            structured_output=structured,
            finish_reason="refusal" if refusal else data.get("status", "completed"),
            refusal_reason=refusal,
            input_tokens=int(usage.get("input_tokens", 0)),
            cached_input_tokens=int((usage.get("input_tokens_details") or {}).get("cached_tokens", 0)),
            output_tokens=max(0, int(usage.get("output_tokens", 0)) - reasoning_tokens),
            continuation_state={"output": data.get("output", [])} if tool_calls else None,
            reasoning_tokens=reasoning_tokens,
        )
