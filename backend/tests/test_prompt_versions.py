import json
import tempfile
import unittest
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

from app.adapters.providers.anthropic_adapter import AnthropicMessagesAdapter
from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.api.main import create_app
from app.application import prompts
from app.application.ports.providers import ModelRequest
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.application.services.routing_service import context_token_sections
from app.bootstrap.container import build_container
from app.domain.models import RunMode
from app.domain.providers import AgentRole, ApiProfile, ModelCapabilities, ModelProfile, ProviderId, ProviderProfile
from tests.test_execution_router import RoleCountingGateway
from tests.test_tool_loop import _publish_orders

KNOWN = {t.template_id: t.version for t in prompts.ALL_TEMPLATES}


class TestTemplates(unittest.TestCase):
    def test_ids_are_unique_and_versions_follow_the_text(self) -> None:
        self.assertEqual(len(KNOWN), len(prompts.ALL_TEMPLATES))
        a = prompts.PromptTemplate("x", "Be concise.")
        self.assertEqual(a.version, prompts.PromptTemplate("x", "Be concise.").version)
        self.assertNotEqual(a.version, prompts.PromptTemplate("x", "Be concise!").version)

    def test_shared_instructions_reach_the_specialist(self) -> None:
        for phrase in ("business_timezone", "denied (do not retry", "safe_message"):
            self.assertIn(phrase, prompts.SPECIALIST.text)
        # The reviewer and the delegate get the same tool rules, but each has its own role and output format.
        for template in (prompts.EVIDENCE_REVIEWER, prompts.DELEGATE):
            for phrase in ("business_timezone", "denied (do not retry", "safe_message"):
                self.assertIn(phrase, template.text)
        self.assertFalse(prompts.EVIDENCE_REVIEWER.text.startswith(prompts.SPECIALIST.text))


class RecordingGateway(RoleCountingGateway):
    def __init__(self) -> None:
        super().__init__()
        self.systems: list[tuple[str, str]] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str):
        system = next((str(m.get("content")) for m in request.messages if m.get("role") == "system"), "")
        self.systems.append((request.role.value, system))
        return super().invoke(request, provider_profile, model_profile, api_key)


class TestEveryModelCallRecordsItsTemplate(unittest.TestCase):
    def _run(self, question: str, mode: RunMode):
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gateway = RecordingGateway()
            c.providers.register(gateway)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            run_id = f"run_pv_{mode.value}"
            InvestigationOrchestrator(c).execute_local(
                run_id=run_id, workspace_id="ws1", question=question, mode=mode, snapshot_id="snap1",
                snapshot={**_publish_orders(c), "snapshot_id": f"snap_pv_{mode.value}_{len(question)}"},
            )
            return c.budget_ledger.list_run_attempts(run_id), gateway.systems

    def test_quick_direct_standard_and_deep_runs(self) -> None:
        for question, mode, expected in (
            ("Which region has the most sales?", RunMode.QUICK, {"specialist"}),
            ("Compare north and south sales", RunMode.QUICK, {"planner", "specialist", "composer"}),
            ("Total sales", RunMode.DEEP, {"planner", "specialist", "evidence_reviewer", "composer"}),
        ):
            with self.subTest(mode=mode.value, question=question):
                attempts, systems = self._run(question, mode)
                self.assertTrue(attempts)
                for attempt in attempts:
                    self.assertIn(attempt.prompt_id, KNOWN, attempt)
                    self.assertEqual(attempt.prompt_version, KNOWN[attempt.prompt_id])
                self.assertTrue(expected <= {a.prompt_id for a in attempts}, {a.prompt_id for a in attempts})
                specialist_systems = [s for role, s in systems if role == "data_specialist"]
                self.assertTrue(all(s.startswith(prompts.SPECIALIST.text) for s in specialist_systems))


class TestApi(unittest.TestCase):
    def test_model_calls_and_templates_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            container.providers.register(RecordingGateway())
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            client = TestClient(create_app(container))
            listed = {t["template_id"]: t["version"] for t in client.get("/api/v1/prompts").json()["templates"]}
            self.assertEqual(listed, KNOWN)
            run_id = client.post("/api/v1/workspaces/ws1/runs", json={"question": "Compare fees", "execute_inline": True}).json()["run_id"]
            calls = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}/model-calls").json()["calls"]
            self.assertTrue(calls)
            self.assertTrue(all(call["prompt_version"] == KNOWN[call["prompt_id"]] for call in calls))
            self.assertEqual(client.get(f"/api/v1/workspaces/ws2/runs/{run_id}/model-calls").status_code, 404)


class TestCachingSignals(unittest.TestCase):
    def _model(self, pid, api):
        return ModelProfile("m", pid, api, (AgentRole.PLANNER,), 100_000, 1000, ModelCapabilities(True, True, True, True, False), Decimal("1"), Decimal("1"), "2026")

    def test_cached_input_tokens_are_read_and_anthropic_marks_the_system_prompt(self) -> None:
        seen: dict = {}

        def anthropic(req: httpx.Request) -> httpx.Response:
            seen.update(json.loads(req.content))
            return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 50, "output_tokens": 2, "cache_read_input_tokens": 40}})

        request = ModelRequest("r", "run", AgentRole.PLANNER, "m", ({"role": "system", "content": "Stable prefix."}, {"role": "user", "content": "hi"}))
        ant = AnthropicMessagesAdapter(transport=httpx.MockTransport(anthropic)).invoke(
            request, ProviderProfile(ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES, "a", "https://api.anthropic.com"), self._model(ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES), "k"
        )
        self.assertEqual(ant.cached_input_tokens, 40)
        self.assertEqual(seen["system"], [{"type": "text", "text": "Stable prefix.", "cache_control": {"type": "ephemeral"}}])
        body = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}], "usage": {"input_tokens": 50, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 32}}}
        oa = OpenAiResponsesAdapter(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))).invoke(
            request, ProviderProfile(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, "o", "https://api.openai.com/v1"), self._model(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES), "k"
        )
        self.assertEqual(oa.cached_input_tokens, 32)

    def test_context_sections_are_measured(self) -> None:
        request = ModelRequest("r", "run", AgentRole.PLANNER, "m", ({"role": "system", "content": "s" * 300}, {"role": "user", "content": "u" * 30}, {"role": "tool", "content": "t" * 90}))
        sections = context_token_sections(request)
        self.assertGreater(sections["context.tokens.system"], sections["context.tokens.tool"])
        self.assertGreater(sections["context.tokens.tool"], sections["context.tokens.user"])
        self.assertEqual(sections["context.tokens.tool_definitions"], 0)


if __name__ == "__main__":
    unittest.main()
