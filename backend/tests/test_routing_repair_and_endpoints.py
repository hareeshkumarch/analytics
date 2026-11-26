import asyncio
from decimal import Decimal
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.adapters.providers.registry import ProviderRegistry
from app.adapters.providers.xai_adapter import XaiResponsesAdapter
from app.api.main import create_app
from app.application.ports.clock import SystemClock
from app.application.ports.providers import ModelRequest
from app.application.services.budget_service import AtomicBudgetLedger
from app.application.services.routing_service import CircuitBreakerManager, CredentialSemaphorePool, RoutingService
from app.bootstrap.container import build_container
from app.domain.providers import (
    AgentRole,
    ApiProfile,
    ModelCapabilities,
    ModelProfile,
    ProviderId,
    ProviderProfile,
    RoutingPolicy,
    RoutingPreset,
)


class TestRoutingRepairAndEndpoints(unittest.TestCase):
    def test_xai_cost_in_usd_ticks_exact_decimal(self) -> None:
        resp_json = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "cost_in_usd_ticks": 1250000},
        }
        xai = XaiResponsesAdapter(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=resp_json)))
        p = ProviderProfile(ProviderId.XAI, ApiProfile.XAI_RESPONSES, "xai", "https://api.x.ai/v1")
        m = ModelProfile(
            "grok-3-mini", ProviderId.XAI, ApiProfile.XAI_RESPONSES, (AgentRole.PLANNER,),
            131072, 4096, ModelCapabilities(True, True, True, True, True), Decimal("0.3"), Decimal("0.5"), "2026-11-17"
        )
        req = ModelRequest("r1", "run1", AgentRole.PLANNER, "grok-3-mini", ({"role": "user", "content": "hi"},))
        res = xai.invoke(req, p, m, "xai-key")
        self.assertEqual(res.reported_cost_usd, Decimal("0.000125"))

    def test_one_attempt_schema_repair_and_answer_reset_on_fallback(self) -> None:
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not-json"}]}], "usage": {"input_tokens": 5, "output_tokens": 2}})
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"answer\":\"fixed\"}"}]}], "usage": {"input_tokens": 5, "output_tokens": 2}})

        reg = ProviderRegistry()
        reg.register(OpenAiResponsesAdapter(transport=httpx.MockTransport(handler)))
        m = ModelProfile(
            "gpt-4o-2024-11-20", ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, (AgentRole.PLANNER,),
            128000, 4096, ModelCapabilities(True, True, True, True, False), Decimal("2.5"), Decimal("10"), "2026-11-17"
        )
        p = ProviderProfile(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, "oa", "https://api.openai.com/v1")
        ledger = AtomicBudgetLedger()
        ledger.configure_run("run1", max_attempts=6)
        routing = RoutingService(
            providers=reg,
            provider_profiles={ProviderId.OPENAI: p},
            model_profiles={"gpt-4o-2024-11-20": m},
            circuit_breakers=CircuitBreakerManager(SystemClock()),
            budget_ledger=ledger,
            api_keys={ProviderId.OPENAI: "sk-test"},
            semaphores=CredentialSemaphorePool(),
        )
        policy = RoutingPolicy(1, RoutingPreset.BALANCED, True, {AgentRole.PLANNER: ("gpt-4o-2024-11-20",)}, (ProviderId.OPENAI,))
        req = ModelRequest(
            "r1", "run1", AgentRole.PLANNER, "gpt-4o-2024-11-20", ({"role": "user", "content": "hi"},),
            structured_schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
        )
        resp = routing.execute_step(request=req, policy=policy, estimated_input_tokens=50)
        self.assertEqual(resp.structured_output, {"answer": "fixed"})
        self.assertEqual(calls["n"], 2)

    def test_non_retryable_error_falls_back_to_the_next_model(self) -> None:
        import json as _json

        seen: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            model = _json.loads(req.content)["model"]
            seen.append(model)
            if model == "gpt-a":
                return httpx.Response(400, json={"error": {"message": "model not available for this key"}})
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}], "usage": {"input_tokens": 5, "output_tokens": 2}})

        reg = ProviderRegistry()
        reg.register(OpenAiResponsesAdapter(transport=httpx.MockTransport(handler)))
        caps = ModelCapabilities(True, True, True, True, False)
        models = {
            name: ModelProfile(name, ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, (AgentRole.PLANNER,), 128000, 4096, caps, Decimal("1"), Decimal("1"), "2026-11-17")
            for name in ("gpt-a", "gpt-b")
        }
        ledger = AtomicBudgetLedger()
        ledger.configure_run("run1", max_attempts=6)
        routing = RoutingService(
            providers=reg,
            provider_profiles={ProviderId.OPENAI: ProviderProfile(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, "oa", "https://api.openai.com/v1")},
            model_profiles=models,
            circuit_breakers=CircuitBreakerManager(SystemClock()),
            budget_ledger=ledger,
            api_keys={ProviderId.OPENAI: "sk-test"},
            semaphores=CredentialSemaphorePool(),
        )
        req = ModelRequest("r1", "run1", AgentRole.PLANNER, "gpt-a", ({"role": "user", "content": "hi"},))
        with_fallback = RoutingPolicy(1, RoutingPreset.BALANCED, True, {AgentRole.PLANNER: ("gpt-a", "gpt-b")}, (ProviderId.OPENAI,))
        self.assertEqual(routing.execute_step(request=req, policy=with_fallback, estimated_input_tokens=50).text, "ok")
        self.assertEqual(seen, ["gpt-a", "gpt-b"])
        without = RoutingPolicy(1, RoutingPreset.BALANCED, False, {AgentRole.PLANNER: ("gpt-a", "gpt-b")}, (ProviderId.OPENAI,))
        with self.assertRaises(Exception):
            routing.execute_step(request=req, policy=without, estimated_input_tokens=50)
        self.assertEqual(seen, ["gpt-a", "gpt-b", "gpt-a"])

    def test_latency_and_quality_presets_order_candidates(self) -> None:
        def model(name: str, reasoning: bool, context: int) -> ModelProfile:
            caps = ModelCapabilities(True, True, True, True, reasoning)
            return ModelProfile(name, ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, (AgentRole.PLANNER,), context, 4096, caps, Decimal("1"), Decimal("1"), "2026-11-17")

        models = {m.model_id: m for m in (model("small", False, 32000), model("big", False, 200000), model("thinker", True, 128000))}
        routing = RoutingService(
            providers=ProviderRegistry(),
            provider_profiles={ProviderId.OPENAI: ProviderProfile(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, "oa", "https://api.openai.com/v1")},
            model_profiles=models,
            circuit_breakers=CircuitBreakerManager(SystemClock()),
            budget_ledger=AtomicBudgetLedger(),
            api_keys={ProviderId.OPENAI: "sk-test"},
        )
        routes = {AgentRole.PLANNER: ("small", "big", "thinker")}

        def order(preset: RoutingPreset) -> list[str]:
            policy = RoutingPolicy(1, preset, True, routes, (ProviderId.OPENAI,))
            picked = routing.select_candidates(
                role=AgentRole.PLANNER, policy=policy, requires_structured=False, requires_tools=False,
                requires_streaming=False, estimated_input_tokens=100,
            )
            return [m.model_id for m in picked]

        self.assertEqual(order(RoutingPreset.BALANCED), ["small", "big", "thinker"])
        self.assertEqual(order(RoutingPreset.QUALITY), ["thinker", "big", "small"])
        self.assertEqual(order(RoutingPreset.LATENCY), ["small", "big", "thinker"])
        routing.record_latency("thinker", 300)
        routing.record_latency("big", 900)
        self.assertEqual(order(RoutingPreset.LATENCY), ["thinker", "big", "small"])
        for _ in range(10):
            routing.record_latency("thinker", 5000)
        self.assertEqual(order(RoutingPreset.LATENCY), ["big", "thinker", "small"])

    def test_provider_test_routing_version_and_clarify_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            app = create_app(c)
            app.state.active_run_semaphore = asyncio.Semaphore(0)
            client = TestClient(app)

            r_test = client.post("/api/v1/workspaces/ws1/providers/openai/test")
            self.assertEqual(r_test.status_code, 200)
            self.assertEqual(r_test.json()["status"], "setup_required")
            self.assertEqual(client.post("/api/v1/workspaces/ws1/providers/opnai/test").status_code, 404)
            import time as _time

            app.state.provider_probes[("ws1", ProviderId.OPENAI)] = _time.monotonic()
            self.assertEqual(client.post("/api/v1/workspaces/ws1/providers/openai/test").status_code, 429)

            r_bad_route = client.put("/api/v1/workspaces/ws1/routing", json={"expected_version": 99, "preset": "cost"})
            self.assertEqual(r_bad_route.status_code, 409)

            r_ok_route = client.put("/api/v1/workspaces/ws1/routing", json={"expected_version": 1, "preset": "cost"})
            self.assertEqual(r_ok_route.status_code, 200)
            self.assertEqual(r_ok_route.json()["policy_version"], 2)

            r_run = client.post(
                "/api/v1/workspaces/ws1/runs",
                json={"question": "q", "mode": "quick", "background_async": False},
            )
            run_id = r_run.json()["run_id"]
            r_early_clarification = client.post(
                f"/api/v1/workspaces/ws1/runs/{run_id}/clarify", json={"revision": 0, "answer": "INR"}
            )
            self.assertEqual(r_early_clarification.status_code, 409)
            self.assertTrue(app.state.repo_runs.pause_for_clarification(run_id, "Which currency should I use?"))
            r_clar = client.post(f"/api/v1/workspaces/ws1/runs/{run_id}/clarify", json={"revision": 0, "answer": "INR"})
            self.assertEqual(r_clar.status_code, 200)
            self.assertEqual(r_clar.json()["revision"], 1)


if __name__ == "__main__":
    unittest.main()
