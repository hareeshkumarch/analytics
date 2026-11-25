import json
import tempfile
import unittest
from decimal import Decimal

import httpx

from app.adapters.providers.anthropic_adapter import AnthropicMessagesAdapter
from app.adapters.providers.groq_adapter import GroqChatAdapter
from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.application.ports.providers import ModelRequest, ModelResponse
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.errors import ProviderError
from app.domain.models import RunMode
from app.domain.providers import AgentRole, ApiProfile, ModelCapabilities, ModelProfile, ProviderId, ProviderProfile


def _sse(events: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode() + b"data: [DONE]\n\n"


def _model(provider: ProviderId, api: ApiProfile) -> ModelProfile:
    return ModelProfile("m", provider, api, (AgentRole.ANSWER_COMPOSER,), 100_000, 4096,
                        ModelCapabilities(True, True, True, False, False), Decimal("1"), Decimal("2"), "2026-11-25")


REQUEST = ModelRequest("r", "run", AgentRole.ANSWER_COMPOSER, "m", ({"role": "user", "content": "hi"},))


def _drain(stream):
    pieces = []
    while True:
        try:
            pieces.append(next(stream))
        except StopIteration as done:
            return pieces, done.value


class TestAdapterStreams(unittest.TestCase):
    def _transport(self, body: bytes, seen: list):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
        return httpx.MockTransport(handler)

    def test_chat_completions_stream(self) -> None:
        seen: list = []
        body = _sse([
            {"choices": [{"delta": {"content": "Total "}}]},
            {"choices": [{"delta": {"content": "is 350"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4}},
        ])
        adapter = GroqChatAdapter(transport=self._transport(body, seen))
        profile = ProviderProfile(ProviderId.GROQ, ApiProfile.GROQ_CHAT, "g", "https://api.groq.com/openai/v1")
        pieces, resp = _drain(adapter.stream(REQUEST, profile, _model(ProviderId.GROQ, ApiProfile.GROQ_CHAT), "k"))
        self.assertEqual(pieces, ["Total ", "is 350"])
        self.assertEqual((resp.text, resp.input_tokens, resp.output_tokens), ("Total is 350", 12, 4))
        self.assertTrue(seen[0]["stream"])

    def test_responses_stream(self) -> None:
        body = _sse([
            {"type": "response.output_text.delta", "delta": "Hello"},
            {"type": "response.output_text.delta", "delta": " world"},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 9, "output_tokens": 2}}},
        ])
        adapter = OpenAiResponsesAdapter(transport=self._transport(body, []))
        profile = ProviderProfile(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, "o", "https://api.openai.com/v1")
        pieces, resp = _drain(adapter.stream(REQUEST, profile, _model(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES), "k"))
        self.assertEqual("".join(pieces), "Hello world")
        self.assertEqual((resp.input_tokens, resp.output_tokens), (9, 2))

    def test_anthropic_stream(self) -> None:
        body = _sse([
            {"type": "message_start", "message": {"usage": {"input_tokens": 20}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Fees "}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "match."}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
        ])
        adapter = AnthropicMessagesAdapter(transport=self._transport(body, []))
        profile = ProviderProfile(ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES, "a", "https://api.anthropic.com")
        pieces, resp = _drain(adapter.stream(REQUEST, profile, _model(ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES), "k"))
        self.assertEqual(pieces, ["Fees ", "match."])
        self.assertEqual((resp.finish_reason, resp.input_tokens, resp.output_tokens), ("end_turn", 20, 3))

    def test_non_streaming_reply_is_reported_as_unsupported(self) -> None:
        adapter = GroqChatAdapter(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": []})))
        profile = ProviderProfile(ProviderId.GROQ, ApiProfile.GROQ_CHAT, "g", "https://api.groq.com/openai/v1")
        with self.assertRaises(ProviderError) as ctx:
            _drain(adapter.stream(REQUEST, profile, _model(ProviderId.GROQ, ApiProfile.GROQ_CHAT), "k"))
        self.assertEqual(ctx.exception.code, "stream_unsupported")

    def test_stream_http_error_is_classified(self) -> None:
        adapter = GroqChatAdapter(transport=httpx.MockTransport(lambda r: httpx.Response(429, text="slow down")))
        profile = ProviderProfile(ProviderId.GROQ, ApiProfile.GROQ_CHAT, "g", "https://api.groq.com/openai/v1")
        with self.assertRaises(ProviderError) as ctx:
            _drain(adapter.stream(REQUEST, profile, _model(ProviderId.GROQ, ApiProfile.GROQ_CHAT), "k"))
        self.assertTrue(ctx.exception.retryable)


class _StreamingGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.streams = 0

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=f"Executed {request.role.value} step", input_tokens=10, output_tokens=5)

    def stream(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str):
        self.streams += 1
        if self.fail_first and self.streams == 1:
            yield "Broken partial "
            raise ProviderError("dropped", code="provider_transient_error", retryable=True)
        words = ["The", " orders", " total", " is", " grounded."] * 30
        for word in words:
            yield word
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="".join(words), input_tokens=50, output_tokens=150)


class TestComposerStreaming(unittest.TestCase):
    def _run(self, gateway: _StreamingGateway):
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            c.providers.register(gateway)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events: list = []
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_stream", workspace_id="ws1", question="Total?", mode=RunMode.QUICK, emit_event=events.append,
            )
        tail = [e for e in events if e.type.value in ("answer.reset", "answer.delta") and e.payload.get("draft_id", "final_answer") == "final_answer"]
        return answer, tail

    def test_composer_answer_streams_in_batches(self) -> None:
        answer, tail = self._run(_StreamingGateway())
        deltas = [e.payload["text"] for e in tail if e.type.value == "answer.delta"]
        self.assertEqual(tail[0].type.value, "answer.reset")
        self.assertGreater(len(deltas), 2)
        self.assertLess(len(deltas), 150)
        self.assertEqual("".join(deltas).strip(), answer.summary_markdown)

    def test_failed_stream_resets_before_the_retry(self) -> None:
        answer, tail = self._run(_StreamingGateway(fail_first=True))
        kinds = [e.type.value for e in tail]
        last_reset = len(kinds) - 1 - kinds[::-1].index("answer.reset")
        final_text = "".join(e.payload["text"] for e in tail[last_reset + 1:])
        self.assertNotIn("Broken partial", final_text)
        self.assertEqual(final_text.strip(), answer.summary_markdown)


if __name__ == "__main__":
    unittest.main()
