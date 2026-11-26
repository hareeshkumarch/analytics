import dataclasses
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.adapters.orchestration import supervisor as supervisor_module
from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.limits import DEEP_BUDGET_LIMITS, QUICK_BUDGET_LIMITS
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from tests.test_tool_loop import QUERY, _publish_orders


class HeavyAnalystGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, analyst_tokens: int) -> None:
        self.analyst_tokens = analyst_tokens
        self.roles: list[str] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.roles.append(request.role.value)
        tokens = self.analyst_tokens if request.role.value == "data_specialist" else 100
        names = {t.name for t in request.tools or ()}
        if "run_query_ir" in names and not [m for m in request.messages if m.get("role") == "tool"]:
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=(ToolCallResult("q", "run_query_ir", QUERY),), input_tokens=tokens)
        found = re.findall(r"ev_[0-9a-f]{12}", " ".join(str(m.get("content")) for m in request.messages))
        text = f"North leads with 12.5 [{found[-1]}]." if found else "Reviewed."
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=text, input_tokens=tokens, output_tokens=10)


class TestProfiles(unittest.TestCase):
    def test_modes_have_their_own_ceilings_and_a_final_answer_reserve(self) -> None:
        self.assertLess(QUICK_BUDGET_LIMITS.total_tokens, DEEP_BUDGET_LIMITS.total_tokens)
        for limits in (QUICK_BUDGET_LIMITS, DEEP_BUDGET_LIMITS):
            self.assertGreater(limits.finalization_reserve_tokens, 0)
            self.assertLess(limits.finalization_reserve_tokens, limits.total_tokens)


class TestTokenCeiling(unittest.TestCase):
    def test_hitting_the_ceiling_stops_new_work_and_still_answers(self) -> None:
        small = dataclasses.replace(DEEP_BUDGET_LIMITS, total_tokens=200_000, concurrent_specialists=1)
        with tempfile.TemporaryDirectory() as td, patch.object(supervisor_module, "DEEP_BUDGET_LIMITS", small):
            os.environ["AGENT_RUNTIME"] = "deepagents"
            c = build_container(artifact_root=td)
            gateway = HeavyAnalystGateway(analyst_tokens=85_000)
            c.providers.register(gateway)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events: list = []
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_ceiling", workspace_id="ws1", question="Total sales by region", mode=RunMode.DEEP,
                snapshot_id="snap1", snapshot={**_publish_orders(c), "snapshot_id": "snap_ceiling"}, emit_event=events.append,
            )
        finished = [e.payload for e in events if e.type.value == "run.finished"][-1]
        self.assertEqual(finished["status"], "completed")
        self.assertTrue(any("token budget" in lim for lim in answer.limitations), answer.limitations)
        self.assertNotIn("evidence_reviewer", gateway.roles)
        self.assertIn("answer_composer", gateway.roles)
        self.assertIn("North leads with 12.5", answer.summary_markdown)
        self.assertEqual(answer.outcome, "partial_success")
        warnings = [e.payload for e in events if e.type.value == "budget.warning"]
        self.assertEqual([w["resource"] for w in warnings], ["tokens"])
        self.assertGreaterEqual(warnings[0]["used"], 0.8 * warnings[0]["limit"])


class TestWorkspaceDailyQuota(unittest.TestCase):
    def test_runs_beyond_the_daily_limit_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"WORKSPACE_DAILY_RUN_LIMIT": "2"}):
            client = TestClient(create_app(build_container(artifact_root=td)))
            body = {"question": "q", "background_async": False}
            self.assertEqual([client.post("/api/v1/workspaces/ws1/runs", json=body).status_code for _ in range(2)], [202, 202])
            refused = client.post("/api/v1/workspaces/ws1/runs", json=body)
            self.assertEqual(refused.status_code, 429)
            self.assertIn("daily limit of 2", refused.text)
            self.assertEqual(client.post("/api/v1/workspaces/ws2/runs", json=body).status_code, 202)

    def test_no_quota_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ.pop("WORKSPACE_DAILY_RUN_LIMIT", None)
            os.environ.pop("WORKSPACE_DAILY_COST_LIMIT_USD", None)
            client = TestClient(create_app(build_container(artifact_root=td)))
            for _ in range(3):
                self.assertEqual(client.post("/api/v1/workspaces/ws1/runs", json={"question": "q", "background_async": False}).status_code, 202)


if __name__ == "__main__":
    unittest.main()
