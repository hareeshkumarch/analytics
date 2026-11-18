import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.providers.anthropic_adapter import AnthropicMessagesAdapter
from app.adapters.providers.gemini_adapter import GeminiInteractionsAdapter
from app.adapters.providers.groq_adapter import GroqChatAdapter
from app.adapters.providers.kimi_adapter import KimiChatAdapter
from app.adapters.providers.nim_adapter import NvidiaNimAdapter
from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.adapters.providers.registry import ProviderRegistry
from app.adapters.providers.xai_adapter import XaiResponsesAdapter
from app.application.ports.clock import SystemClock
from app.application.ports.providers import ModelRequest, ToolDefinition
from app.application.services.budget_service import AtomicBudgetLedger
from app.application.services.routing_service import CircuitBreakerManager, RoutingService
from app.domain.errors import ProviderError, ValidationError
from app.domain.providers import (
    AgentRole,
    ApiProfile,
    CircuitStatus,
    ModelCapabilities,
    ModelProfile,
    ProviderId,
    ProviderProfile,
    RoutingPolicy,
    RoutingPreset,
)


def _mock_transport(response_json: dict, status_code: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda req: httpx.Response(status_code=status_code, json=response_json))


class TestProvidersAndRouting(unittest.TestCase):
    def test_all_seven_provider_adapters_contract(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}

        oa_resp = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"answer":"ok"}'}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        oa = OpenAiResponsesAdapter(transport=_mock_transport(oa_resp))
        m_oa = ModelProfile(
            "gpt-4o-2024-11-20", ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES,
            (AgentRole.PLANNER,), 128000, 4096,
            ModelCapabilities(True, True, True, True, False), Decimal("2.5"), Decimal("10"), "2026-11-17"
        )
        p_oa = ProviderProfile(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, "oa", "https://api.openai.com/v1")
        req = ModelRequest("r1", "run1", AgentRole.PLANNER, "gpt-4o-2024-11-20", ({"role": "user", "content": "hi"},), structured_schema=schema)
        res = oa.invoke(req, p_oa, m_oa, "sk-test")
        self.assertEqual(res.structured_output, {"answer": "ok"})

        ant_resp = {
            "stop_reason": "refusal",
            "content": [{"type": "text", "text": "Cannot comply"}],
            "usage": {"input_tokens": 10, "output_tokens": 3},
        }
        ant = AnthropicMessagesAdapter(transport=_mock_transport(ant_resp))
        m_ant = ModelProfile(
            "claude-sonnet-4-5-20250929", ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES,
            (AgentRole.PLANNER,), 200000, 4096,
            ModelCapabilities(True, True, True, True, True), Decimal("3"), Decimal("15"), "2026-11-17"
        )
        p_ant = ProviderProfile(ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES, "ant", "https://api.anthropic.com")
        res_ant = ant.invoke(req, p_ant, m_ant, "sk-ant")
        self.assertEqual(res_ant.refusal_reason, "Model refused request")

        gem_resp = {
            "status": "completed",
            "outputs": [{"type": "text", "text": '{"answer":"gem"}'}],
            "usage": {"total_input_tokens": 8, "total_output_tokens": 4, "total_thought_tokens": 2},
        }
        gem = GeminiInteractionsAdapter(transport=_mock_transport(gem_resp))
        m_gem = ModelProfile(
            "gemini-2.5-flash", ProviderId.GEMINI, ApiProfile.GEMINI_INTERACTIONS,
            (AgentRole.PLANNER,), 1000000, 4096,
            ModelCapabilities(True, True, True, True, True), Decimal("0.15"), Decimal("0.60"), "2026-11-17"
        )
        p_gem = ProviderProfile(ProviderId.GEMINI, ApiProfile.GEMINI_INTERACTIONS, "gem", "https://generativelanguage.googleapis.com")
        res_gem = gem.invoke(req, p_gem, m_gem, "gem-key")
        self.assertEqual(res_gem.reasoning_tokens, 2)

        groq = GroqChatAdapter(transport=_mock_transport({"choices": [{"message": {"content": "{}"}}]}))
        m_groq = ModelProfile(
            "llama-3.3-70b-versatile", ProviderId.GROQ, ApiProfile.GROQ_CHAT,
            (AgentRole.DATA_SPECIALIST,), 128000, 4096,
            ModelCapabilities(True, True, True, False, False), Decimal("0.59"), Decimal("0.79"), "2026-11-17"
        )
        req_both = ModelRequest(
            "r2", "run1", AgentRole.DATA_SPECIALIST, "llama-3.3-70b-versatile",
            ({"role": "user", "content": "hi"},),
            tools=(ToolDefinition("run_query_ir", "run", {"type": "object"}),),
            structured_schema=schema,
        )
        with self.assertRaises(ValidationError):
            groq.build_payload(req_both, m_groq)

        xai = XaiResponsesAdapter(transport=_mock_transport(oa_resp))
        p_xai = ProviderProfile(ProviderId.XAI, ApiProfile.XAI_RESPONSES, "xai", "https://api.x.ai/v1")
        m_xai = ModelProfile("grok-3-mini", ProviderId.XAI, ApiProfile.XAI_RESPONSES, (AgentRole.PLANNER,), 131072, 4096, ModelCapabilities(True, True, True, True, True), Decimal("0.3"), Decimal("0.5"), "2026-11-17")
        self.assertEqual(xai.invoke(req, p_xai, m_xai, "xai-key").structured_output, {"answer": "ok"})

        kimi_resp = {"choices": [{"message": {"content": '{"answer":"k"}', "reasoning_content": "trace"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
        kimi = KimiChatAdapter(transport=_mock_transport(kimi_resp))
        p_kimi = ProviderProfile(ProviderId.KIMI, ApiProfile.KIMI_CHAT, "kimi", "https://api.moonshot.ai/v1")
        m_kimi = ModelProfile("kimi-k3", ProviderId.KIMI, ApiProfile.KIMI_CHAT, (AgentRole.PLANNER,), 1000000, 4096, ModelCapabilities(True, True, True, False, True), Decimal("3"), Decimal("15"), "2026-11-17")
        res_kimi = kimi.invoke(req, p_kimi, m_kimi, "kimi-key")
        self.assertEqual(res_kimi.continuation_state, {"reasoning_content": "trace"})

        nim_resp = {"choices": [{"message": {"content": '{"answer":"nim"}'}}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
        nim = NvidiaNimAdapter(transport=_mock_transport(nim_resp))
        p_nim = ProviderProfile(ProviderId.NVIDIA_NIM, ApiProfile.NVIDIA_NIM_CHAT, "nim", "https://integrate.api.nvidia.com/v1")
        m_nim = ModelProfile("meta/llama-3.3-70b-instruct", ProviderId.NVIDIA_NIM, ApiProfile.NVIDIA_NIM_CHAT, (AgentRole.DATA_SPECIALIST,), 128000, 4096, ModelCapabilities(True, True, True, False, False), None, None, "2026-11-17")
        self.assertEqual(nim.invoke(req, p_nim, m_nim, "nim-key").structured_output, {"answer": "nim"})

    def test_circuit_breaker_opens_after_five_transient_failures(self) -> None:
        cb = CircuitBreakerManager(SystemClock(), failure_threshold=5, window_seconds=60.0, open_seconds=30.0)
        for _ in range(5):
            cb.record_transient_failure("openai:gpt-4o")
        self.assertEqual(cb.get_state("openai:gpt-4o").status, CircuitStatus.OPEN)
        self.assertFalse(cb.try_acquire("openai:gpt-4o"))


if __name__ == "__main__":
    unittest.main()
