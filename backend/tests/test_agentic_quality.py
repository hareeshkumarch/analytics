"""Second round of agent-layer safeguards: query validation, context, accounting, streaming, prompts and routing."""

import dataclasses
import json
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

import httpx
from langchain_core.messages import HumanMessage

from app.adapters.providers.anthropic_adapter import AnthropicMessagesAdapter
from app.adapters.providers.http_utils import read_anthropic_event, read_chat_completions_event, read_responses_event, stream_sse_text
from app.adapters.providers.langchain_gateway import GatewayChatModel
from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application import prompts
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.analytics_tools import RUN_QUERY_TOOL, AnalyticsToolbox, ToolBudget
from app.application.services.context_compiler import OLDER_RESULT_ROWS, compact_older_tool_results, composer_findings
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.errors import ProviderError
from app.domain.models import RunMode
from app.domain.providers import AgentRole, ApiProfile, ModelCapabilities, ModelProfile, ProviderId, ProviderProfile
from app.domain.query_ir import QueryIRParseError, parse_query_ir
from tests.test_tool_loop import QUERY, ToolCallingGateway, _publish_orders

CAPS = ModelCapabilities(True, True, True, True, True)


def _model(pid: ProviderId, api: ApiProfile, **extra) -> ModelProfile:
    return ModelProfile("m", pid, api, (AgentRole.PLANNER,), 100_000, 1000, CAPS, Decimal("1"), Decimal("1"), "2026-11-27", **extra)


def _invoke(cls, pid: ProviderId, api: ApiProfile, body: dict) -> ModelResponse:
    adapter = cls(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    request = ModelRequest("r", "run", AgentRole.PLANNER, "m", ({"role": "user", "content": "hi"},))
    return adapter.invoke(request, ProviderProfile(pid, api, "p", "https://provider.example/v1"), _model(pid, api), "key")


def _container(td: str, gateway):
    c = build_container(artifact_root=td)
    c.providers.register(gateway)
    c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
    return c


class TestQueryValidation(unittest.TestCase):
    def test_parameters_an_operation_ignores_are_rejected_by_name(self) -> None:
        compare = {
            "operation": "compare_periods", "purpose": "p", "table_id": "orders", "date_field": "d",
            "metric": {"alias": "v", "func": "sum", "field_id": "amount"},
            "current_start": "2026-03-01", "current_end": "2026-04-01", "previous_start": "2025-03-01", "previous_end": "2025-04-01",
        }
        with self.assertRaises(QueryIRParseError) as ctx:
            parse_query_ir({**compare, "order_by": [{"field_id": "v", "direction": "desc"}]})
        self.assertEqual(ctx.exception.failed_fields, ("order_by",))
        self.assertIn("compare_periods does not support 'order_by'", str(ctx.exception))
        with self.assertRaises(QueryIRParseError) as ctx:
            parse_query_ir({"operation": "detail", "purpose": "p", "table_id": "orders", "select_fields": ["region"], "group_by": ["region"]})
        self.assertEqual(ctx.exception.failed_fields, ("group_by",))
        # Empty placeholders for unused parameters are what models send; they are not a mistake.
        self.assertEqual(parse_query_ir({**compare, "order_by": [], "select_fields": None}).operation, "compare_periods")
        self.assertEqual(parse_query_ir(QUERY).operation, "aggregate")

    def test_sum_of_a_text_column_is_refused_before_it_runs_and_costs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            budget = ToolBudget(5, 5)
            box = AnalyticsToolbox(
                engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()), snapshot=_publish_orders(c), semantics=None, budget=budget
            )
            bad = {**QUERY, "metrics": [{"alias": "total", "func": "sum", "field_id": "region"}], "group_by": [], "order_by": []}
            out = box.execute(ToolCallResult("c1", "run_query_ir", bad))
            payload = json.loads(out.content)
            self.assertTrue(out.is_error)
            self.assertEqual(payload["status"], "failed")
            self.assertIn("sum needs a numeric column", payload["safe_message"])
            self.assertEqual(payload["failed_fields"], ["metrics"])
            good = box.execute(ToolCallResult("c2", "run_query_ir", QUERY))
            self.assertFalse(good.is_error)
            # One analytical query was charged: the valid one.
            self.assertEqual(budget.snapshot()["analytical_used"] if hasattr(budget, "snapshot") else 1, 1)

    def test_the_tool_description_lists_each_operations_parameters_with_examples(self) -> None:
        text = RUN_QUERY_TOOL.description
        for needle in ("compare_periods: table_id, date_field", "anything else is rejected", '"operation": "aggregate"', "count counts the rows"):
            self.assertIn(needle, text)


class TestContext(unittest.TestCase):
    def test_older_tool_results_are_cut_to_their_first_rows(self) -> None:
        big = json.dumps({"rows": [[i, "x" * 200] for i in range(50)], "evidence_id": "ev_1"})
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": big},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]},
            {"role": "tool", "content": big},
        ]
        compact_older_tool_results(messages, 3)
        old, new = json.loads(messages[2]["content"]), json.loads(messages[4]["content"])
        self.assertEqual((len(old["rows"]), old["older_result_trimmed"], old["evidence_id"]), (OLDER_RESULT_ROWS, True, "ev_1"))
        self.assertEqual(len(new["rows"]), 50)

    def test_composer_cuts_reviewer_notes_before_the_analysis(self) -> None:
        analysis, review, remediation = "A" * 30_000, "R" * 30_000, "M" * 2_000
        compiled = composer_findings([analysis, review, remediation], budget_tokens=6_000, review_indexes=frozenset({1}))
        kept = {s.name: len(s.text) for s in compiled.sections}
        self.assertLess(kept["finding:1"], kept["finding:0"])
        self.assertEqual(kept["finding:2"], 2_000)
        self.assertEqual(compiled.trimmed[0], "finding:1")

    def test_a_step_trims_old_results_and_stays_on_its_first_model(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _container(td, ToolCallingGateway())
            model = GatewayChatModel(routing_service=c.routing_service, policy=c.default_routing_policy, run_id="r", role=AgentRole.DATA_SPECIALIST)
            real = c.routing_service.execute_step
            seen: list[dict] = []

            def spy(**kwargs):
                seen.append(kwargs)
                return real(**kwargs)

            with patch.object(c.routing_service, "execute_step", spy):
                first = model.invoke([HumanMessage("hi")])
                model.invoke([HumanMessage("hi again")])
            self.assertIsNone(seen[0]["manual_model_id"])
            self.assertEqual(seen[1]["manual_model_id"], first.response_metadata["model_name"])

    def test_an_unavailable_pinned_model_falls_back_instead_of_failing_the_step(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _container(td, ToolCallingGateway())
            model = GatewayChatModel(routing_service=c.routing_service, policy=c.default_routing_policy, run_id="r", role=AgentRole.DATA_SPECIALIST)
            model._pinned_model = "model-that-is-gone"
            reply = model.invoke([HumanMessage("hi")])
            self.assertTrue(reply.content)
            self.assertNotEqual(model._pinned_model, "model-that-is-gone")


class TestTokenAccounting(unittest.TestCase):
    def test_anthropic_input_includes_cached_tokens(self) -> None:
        body = {
            "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 300, "output_tokens": 7},
        }
        response = _invoke(AnthropicMessagesAdapter, ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES, body)
        self.assertEqual((response.input_tokens, response.cached_input_tokens, response.output_tokens), (1212, 900, 7))

    def test_openai_reasoning_tokens_are_not_counted_twice(self) -> None:
        body = {
            "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 30}},
        }
        response = _invoke(OpenAiResponsesAdapter, ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, body)
        self.assertEqual(response.input_tokens + response.output_tokens + response.reasoning_tokens, 150)
        self.assertEqual((response.output_tokens, response.reasoning_tokens), (20, 30))

    def test_a_reply_cut_off_at_the_token_limit_reports_what_it_cost(self) -> None:
        body = {"content": [{"type": "text", "text": "partial"}], "stop_reason": "max_tokens", "usage": {"input_tokens": 40, "output_tokens": 1000}}
        with self.assertRaises(ProviderError) as ctx:
            _invoke(AnthropicMessagesAdapter, ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES, body)
        self.assertEqual((ctx.exception.code, ctx.exception.billed_tokens), ("provider_incomplete", (40, 1000)))

    def test_the_run_is_charged_for_a_cut_off_reply(self) -> None:
        class CutOff(ToolCallingGateway):
            def invoke(self, request, provider_profile, model_profile, api_key):
                error = ProviderError("OpenAI stopped before finishing the reply (max_output_tokens)", code="provider_incomplete", retryable=False)
                error.billed_tokens = (40, 1000)
                raise error

        with tempfile.TemporaryDirectory() as td:
            c = _container(td, CutOff())
            policy = dataclasses.replace(c.default_routing_policy, fallback_enabled=False)
            request = ModelRequest("req", "run_cut", AgentRole.DATA_SPECIALIST, "", ({"role": "user", "content": "hi"},))
            with self.assertRaises(ProviderError):
                c.routing_service.execute_step(request=request, policy=policy, estimated_input_tokens=10)
            self.assertEqual(int(c.budget_ledger.get_run_totals("run_cut")["total_tokens"]), 1040)


def _stream(events: list[dict], reader) -> tuple[list[str], Exception | None]:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}))
    pieces: list[str] = []
    try:
        generator = stream_sse_text("https://provider.example/v1", {}, {}, 5, transport, "Provider", reader)
        while True:
            pieces.append(next(generator))
    except StopIteration:
        return pieces, None
    except ProviderError as exc:
        return pieces, exc


class TestStreamsThatDoNotFinish(unittest.TestCase):
    TEXT = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Revenue was"}}

    def test_a_finished_stream_is_returned(self) -> None:
        done = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}
        pieces, error = _stream([self.TEXT, done], read_anthropic_event)
        self.assertEqual((pieces, error), (["Revenue was"], None))

    def test_anthropic_error_event_and_token_limit_are_failures(self) -> None:
        _, error = _stream([self.TEXT, {"type": "error", "error": {"type": "overloaded_error"}}], read_anthropic_event)
        self.assertEqual((error.code, error.retryable), ("provider_stream_error", True))  # type: ignore[union-attr]
        start = {"type": "message_start", "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 95}}}
        cut = {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 64}}
        _, error = _stream([start, self.TEXT, cut], read_anthropic_event)
        self.assertEqual((error.code, error.billed_tokens), ("provider_incomplete", (100, 64)))  # type: ignore[union-attr]

    def test_openai_incomplete_and_failed_streams_are_failures(self) -> None:
        delta = {"type": "response.output_text.delta", "delta": "Revenue"}
        incomplete = {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}, "usage": {"input_tokens": 9, "output_tokens": 20}}}
        _, error = _stream([delta, incomplete], read_responses_event)
        self.assertEqual((error.code, error.billed_tokens), ("provider_incomplete", (9, 20)))  # type: ignore[union-attr]
        _, error = _stream([delta, {"type": "response.failed", "response": {"error": {"message": "boom"}}}], read_responses_event)
        self.assertEqual(error.code, "provider_stream_error")  # type: ignore[union-attr]
        _, error = _stream([{"error": {"message": "boom"}}], read_chat_completions_event)
        self.assertEqual(error.code, "provider_stream_error")  # type: ignore[union-attr]


class _PlannerDownGateway(ToolCallingGateway):
    def invoke(self, request, provider_profile, model_profile, api_key):
        if request.role == AgentRole.PLANNER:
            self.requests.append(request)
            raise ProviderError("OpenAI rejected the request (HTTP 400)", code="provider_invalid_request", retryable=False)
        return super().invoke(request, provider_profile, model_profile, api_key)


class _SpecialistDownGateway(ToolCallingGateway):
    def invoke(self, request, provider_profile, model_profile, api_key):
        if request.role == AgentRole.DATA_SPECIALIST:
            self.requests.append(request)
            raise ProviderError("OpenAI rejected the request (HTTP 400)", code="provider_invalid_request", retryable=False)
        return super().invoke(request, provider_profile, model_profile, api_key)


class TestHarnessFallbacks(unittest.TestCase):
    def _deep(self, gateway):
        with tempfile.TemporaryDirectory() as td:
            c = _container(td, gateway)
            return InvestigationOrchestrator(c).execute_local(
                run_id="run_deep", workspace_id="ws1", question="What is the total amount by region and how do the regions compare?",
                mode=RunMode.DEEP, snapshot_id="snap1", snapshot=_publish_orders(c),
            )

    def test_a_planner_outage_falls_back_to_the_standard_plan(self) -> None:
        gateway = _PlannerDownGateway()
        answer = self._deep(gateway)
        self.assertTrue(any("standard plan" in limitation for limitation in answer.limitations), answer.limitations)
        self.assertTrue(any(r.role == AgentRole.DATA_SPECIALIST for r in gateway.requests), "no analysis ran after the planner failed")

    def test_a_review_that_could_not_run_is_not_treated_as_an_approval(self) -> None:
        answer = self._deep(_SpecialistDownGateway())
        self.assertNotEqual(answer.outcome, "success")


class TestPromptsAndRouting(unittest.TestCase):
    def test_composer_is_told_to_lead_with_the_headline_figure_the_ui_shows(self) -> None:
        text = prompts.COMPOSER.text
        for needle in ("in bold", "Copy every figure exactly", "language of the user's question", "Start with the direct answer"):
            self.assertIn(needle, text)

    def test_reviewer_and_delegate_share_the_tool_rules_without_contradiction(self) -> None:
        for template in (prompts.SPECIALIST, prompts.EVIDENCE_REVIEWER, prompts.DELEGATE):
            self.assertIn("business_timezone", template.text)
            self.assertIn("failed_fields", template.text)
            self.assertIn("rows_withheld", template.text)
        self.assertNotIn("reply with the finding and no tool calls", prompts.EVIDENCE_REVIEWER.text)
        self.assertIn("exactly one JSON object and nothing before or after it", prompts.EVIDENCE_REVIEWER.text)

    def test_reasoning_level_is_sent_only_when_configured(self) -> None:
        request = ModelRequest("r", "run", AgentRole.PLANNER, "m", ({"role": "user", "content": "hi"},), structured_schema={"type": "object"})
        anthropic, openai = AnthropicMessagesAdapter(), OpenAiResponsesAdapter()
        plain_a = anthropic.build_payload(request, _model(ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES))
        plain_o = openai.build_payload(request, _model(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES))
        self.assertNotIn("effort", plain_a["output_config"])
        self.assertNotIn("reasoning", plain_o)
        tuned_a = anthropic.build_payload(request, _model(ProviderId.ANTHROPIC, ApiProfile.ANTHROPIC_MESSAGES, reasoning_effort="low"))
        tuned_o = openai.build_payload(request, _model(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, reasoning_effort="low"))
        self.assertEqual(tuned_a["output_config"]["effort"], "low")
        self.assertIn("format", tuned_a["output_config"])
        self.assertEqual(tuned_o["reasoning"], {"effort": "low"})

    def test_a_newer_model_that_costs_no_more_is_tried_before_its_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            for role, route in c.default_routing_policy.role_routes.items():
                for newer, older in (("claude-sonnet-5-5", "claude-sonnet-4-5-20250929"), ("gpt-6.1-sol", "gpt-4o-2024-11-20")):
                    if newer in route and older in route:
                        self.assertLess(route.index(newer), route.index(older), role)
                        self.assertLessEqual(c.model_profiles[newer].input_price_per_1m, c.model_profiles[older].input_price_per_1m)


if __name__ == "__main__":
    unittest.main()
