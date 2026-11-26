import json
import unittest
from decimal import Decimal

import httpx

from app.adapters.providers.anthropic_adapter import AnthropicMessagesAdapter
from app.adapters.providers.gemini_adapter import GeminiInteractionsAdapter
from app.adapters.providers.groq_adapter import GroqChatAdapter
from app.adapters.providers.kimi_adapter import KimiChatAdapter
from app.adapters.providers.nim_adapter import NvidiaNimAdapter
from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.adapters.providers.registry import ProviderRegistry
from app.adapters.providers.xai_adapter import XaiResponsesAdapter
from app.application.ports.clock import SystemClock
from app.application.ports.providers import ModelRequest
from app.application.services.budget_service import AtomicBudgetLedger
from app.application.services.routing_service import CircuitBreakerManager, CredentialSemaphorePool, RoutingService
from app.domain.errors import ProviderError
from app.domain.providers import AgentRole, ApiProfile, ModelCapabilities, ModelProfile, ProviderId, ProviderProfile, RoutingPolicy, RoutingPreset

ADAPTERS = (
    (OpenAiResponsesAdapter, ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES),
    (XaiResponsesAdapter, ProviderId.XAI, ApiProfile.XAI_RESPONSES),
    (AnthropicMessagesAdapter, ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES),
    (GeminiInteractionsAdapter, ProviderId.GEMINI, ApiProfile.GEMINI_INTERACTIONS),
    (GroqChatAdapter, ProviderId.GROQ, ApiProfile.GROQ_CHAT),
    (KimiChatAdapter, ProviderId.KIMI, ApiProfile.KIMI_CHAT),
    (NvidiaNimAdapter, ProviderId.NVIDIA_NIM, ApiProfile.NVIDIA_NIM_CHAT),
)
CAPS = ModelCapabilities(True, True, True, True, False)


def _model(name: str, pid: ProviderId, api: ApiProfile) -> ModelProfile:
    return ModelProfile(name, pid, api, (AgentRole.PLANNER,), 100_000, 1000, CAPS, Decimal("1"), Decimal("1"), "2026-11-26")


def _invoke(cls, pid, api, body: bytes):
    adapter = cls(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body, headers={"content-type": "application/json"})))
    request = ModelRequest("r", "run", AgentRole.PLANNER, "m", ({"role": "user", "content": "hi"},))
    return adapter.invoke(request, ProviderProfile(pid, api, "p", "https://provider.example/v1"), _model("m", pid, api), "key")


class TestHttp200FailuresAreNotSuccess(unittest.TestCase):
    def test_every_adapter_rejects_error_bodies_and_malformed_json(self) -> None:
        bodies = {
            "provider_error_body": [{"error": {"message": "overloaded"}}, {"type": "error", "error": {"type": "overloaded_error"}}],
            "provider_failed_response": [{"status": "failed"}],
            "provider_malformed_response": [b"<html>502 Bad Gateway</html>", [1, 2]],
        }
        for cls, pid, api in ADAPTERS:
            for code, cases in bodies.items():
                for body in cases:
                    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                    with self.subTest(adapter=cls.__name__, body=raw[:40]):
                        with self.assertRaises(ProviderError) as ctx:
                            _invoke(cls, pid, api, raw)
                        self.assertEqual(ctx.exception.code, code)

    def test_truncated_replies_are_incomplete(self) -> None:
        truncated = {
            OpenAiResponsesAdapter: {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
            XaiResponsesAdapter: {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
            AnthropicMessagesAdapter: {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "The total is"}]},
            GroqChatAdapter: {"choices": [{"finish_reason": "length", "message": {"content": "The total is"}}]},
            KimiChatAdapter: {"choices": [{"finish_reason": "length", "message": {"content": "The total is"}}]},
            NvidiaNimAdapter: {"choices": [{"finish_reason": "length", "message": {"content": "The total is"}}]},
        }
        for cls, pid, api in ADAPTERS:
            if cls not in truncated:
                continue
            with self.subTest(adapter=cls.__name__), self.assertRaises(ProviderError) as ctx:
                _invoke(cls, pid, api, json.dumps(truncated[cls]).encode())
            self.assertEqual((ctx.exception.code, ctx.exception.retryable), ("provider_incomplete", False))


class TestEmptyReplyFallsBack(unittest.TestCase):
    def test_empty_200_is_retried_on_the_next_model(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            if json.loads(req.content)["model"] == "gpt-empty":
                return httpx.Response(200, json={"status": "completed", "output": []})
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]})

        registry = ProviderRegistry()
        registry.register(OpenAiResponsesAdapter(transport=httpx.MockTransport(handler)))
        api, pid = ApiProfile.OPENAI_RESPONSES, ProviderId.OPENAI
        ledger = AtomicBudgetLedger()
        routing = RoutingService(
            providers=registry,
            provider_profiles={pid: ProviderProfile(pid, api, "oa", "https://api.openai.com/v1")},
            model_profiles={n: _model(n, pid, api) for n in ("gpt-empty", "gpt-ok")},
            circuit_breakers=CircuitBreakerManager(SystemClock()),
            budget_ledger=ledger,
            api_keys={pid: "sk-test"},
            semaphores=CredentialSemaphorePool(),
        )
        policy = RoutingPolicy(1, RoutingPreset.BALANCED, True, {AgentRole.PLANNER: ("gpt-empty", "gpt-ok")}, (pid,))
        request = ModelRequest("r1", "run1", AgentRole.PLANNER, "gpt-empty", ({"role": "user", "content": "hi"},))
        self.assertEqual(routing.execute_step(request=request, policy=policy, estimated_input_tokens=10).text, "ok")
        self.assertTrue(ledger.get_run_totals("run1")["incomplete"])


if __name__ == "__main__":
    unittest.main()
