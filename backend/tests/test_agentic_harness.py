import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.orchestration.deep_agents_bridge import (
    BoundedChildContext,
    DEFAULT_HARNESS_PROFILES,
    build_restricted_deep_agent_config,
    compact_child_context,
    GatewayChatModelBridge,
)
from app.adapters.orchestration.supervisor import ReviewVerdict
from app.application.ports.providers import ModelRequest, ModelResponse, ToolDefinition
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.models import Claim, RunMode
from app.domain.providers import AgentRole, ModelProfile, ProviderId, ProviderProfile


class DeterministicHarnessGateway:
    def __init__(self) -> None:
        self.provider_id = ProviderId.OPENAI

    def invoke(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> ModelResponse:
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=f"Executed {request.role.value} step",
            input_tokens=100,
            output_tokens=20,
        )


class TestAgenticHarness(unittest.TestCase):
    def test_harness_profiles_and_context_compaction_preserve_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            c.providers.register(DeterministicHarnessGateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            bridge = GatewayChatModelBridge(c.routing_service, c.default_routing_policy, "run_h", AgentRole.DATA_SPECIALIST)

            # The config the runtime actually uses (orchestration_service builds the same one).
            harness = build_restricted_deep_agent_config(
                role=AgentRole.DATA_SPECIALIST,
                allowed_tools=(ToolDefinition("run_query_ir", "Run IR", {"type": "object"}),),
                system_prompt="Analyze orders",
            )
            self.assertEqual(harness["harness_profile_id"], DEFAULT_HARNESS_PROFILES[AgentRole.DATA_SPECIALIST].profile_id)
            self.assertTrue(harness["disable_general_purpose_subagent"])
            self.assertTrue(harness["disable_shell_and_interpreter"])

            long_ctx = BoundedChildContext(
                task_id="t1",
                objective="Reconcile",
                snapshot_id="snap1",
                semantic_version_id="sem1",
                approved_schema_summary={},
                relevant_evidence_ids=("ev_1", "ev_2"),
                unresolved_contradictions=("payout_300_in_transit",),
                hard_constraints=("Use Decimal",),
                remaining_tool_calls=10,
                acceptance_criteria="Complete",
                bounded_summary="A" * 2500,
            )
            compacted = compact_child_context(long_ctx, gateway_bridge=bridge)
            self.assertEqual(compacted.relevant_evidence_ids, ("ev_1", "ev_2"))
            self.assertEqual(compacted.unresolved_contradictions, ("payout_300_in_transit",))
            self.assertEqual(compacted.hard_constraints, ("Use Decimal",))

    def test_deep_mode_parallel_fanout_and_bounded_replan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            c.providers.register(DeterministicHarnessGateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            orch = InvestigationOrchestrator(c)

            events = []

            def evaluator(claims: list[Claim], replan_count: int) -> ReviewVerdict:
                if replan_count == 0:
                    return ReviewVerdict(approved=False, gaps=("Verify fee breakdown",))
                return ReviewVerdict(approved=True)

            ans = orch.execute_local(
                run_id="run_deep_1",
                workspace_id="ws1",
                question="Reconcile INR orders and fees",
                mode=RunMode.DEEP,
                emit_event=lambda e: events.append(e),
                replan_evaluator=evaluator,
            )
            self.assertEqual(len(ans.claims), 3)
            self.assertTrue(all("review" not in claim.claim_id for claim in ans.claims))
            plan_events = [e for e in events if e.type.value == "plan.updated"]
            self.assertEqual(len(plan_events), 2)


if __name__ == "__main__":
    unittest.main()
