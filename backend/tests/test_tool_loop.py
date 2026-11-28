import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

for _key in (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
    "MOONSHOT_API_KEY",
    "NVIDIA_NIM_API_KEY",
):
    os.environ.pop(_key, None)

import pyarrow as pa
import pyarrow.ipc as pa_ipc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.providers.message_format import (
    parse_arguments,
    to_anthropic,
    to_chat_completions,
    to_gemini_input,
    to_responses_input,
)
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from app.domain.query_ir import QueryIRParseError, parse_query_ir

QUERY = {
    "operation": "aggregate",
    "purpose": "Total amount by region",
    "table_id": "orders",
    "metrics": [{"alias": "total", "func": "sum", "field_id": "amount"}],
    "group_by": ["region"],
    "order_by": [{"field_id": "total", "direction": "desc"}],
}


def _publish_orders(container) -> dict:
    table = pa.table({"region": ["north", "south", "north"], "amount": [10.0, 5.0, 2.5]})
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    staged = container.artifacts.stage_bytes("job1", "orders.arrow", sink.getvalue().to_pybytes(), "application/octet-stream")
    container.artifacts.publish_staged("job1", staged, "art1__orders")
    return {
        "snapshot_id": "snap1",
        "table_artifacts": {"orders": "art1__orders"},
        "table_details": [
            {
                "name": "orders",
                "row_count": 3,
                "columns": [
                    {"name": "region", "type": "string", "nullable": True},
                    {"name": "amount", "type": "double", "nullable": True},
                ],
            }
        ],
    }


class ToolCallingGateway:
    def __init__(self) -> None:
        self.provider_id = ProviderId.OPENAI
        self.requests: list[ModelRequest] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.requests.append(request)
        tool_msgs = [m for m in request.messages if m.get("role") == "tool"]
        if request.tools and not tool_msgs:
            return ModelResponse(
                provider_id=self.provider_id,
                model_id=model_profile.model_id,
                text="",
                tool_calls=(
                    ToolCallResult("c1", "inspect_schema", {}),
                    ToolCallResult("c2", "run_query_ir", QUERY),
                ),
                continuation_state={"note": "x"},
            )
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=f"North leads. Saw {len(tool_msgs)} tool results.",
        )


class AlwaysToolsGateway(ToolCallingGateway):
    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.requests.append(request)
        if request.tool_choice == "none":
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="Final answer.")
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text="",
            tool_calls=(ToolCallResult(f"c{len(self.requests)}", "run_query_ir", QUERY),),
        )


class TestQueryIRParsing(unittest.TestCase):
    def test_parses_aggregate(self) -> None:
        ir = parse_query_ir(QUERY)
        self.assertEqual(ir.operation, "aggregate")

    def test_rejects_unknown_operation(self) -> None:
        with self.assertRaises(QueryIRParseError):
            parse_query_ir({"operation": "drop_table", "purpose": "x"})


class TestMessageFormat(unittest.TestCase):
    def setUp(self) -> None:
        self.history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "inspect_schema", "arguments": {}}, {"id": "c2", "name": "run_query_ir", "arguments": {"a": 1}}],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "inspect_schema", "content": "{}"},
            {"role": "tool", "tool_call_id": "c2", "name": "run_query_ir", "content": "{}", "is_error": True},
        ]

    def test_anthropic_merges_tool_results(self) -> None:
        system, msgs = to_anthropic(self.history)
        self.assertEqual(system, "sys")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(len(msgs[2]["content"]), 2)
        self.assertTrue(msgs[2]["content"][1]["is_error"])

    def test_responses_items(self) -> None:
        items = to_responses_input(self.history)
        types = [i.get("type") for i in items]
        self.assertEqual(types.count("function_call"), 2)
        self.assertEqual(types.count("function_call_output"), 2)

    def test_chat_completions(self) -> None:
        msgs = to_chat_completions(self.history, "groq")
        self.assertEqual(msgs[2]["tool_calls"][1]["function"]["arguments"], '{"a": 1}')
        self.assertEqual(msgs[3]["role"], "tool")

    def test_gemini_uses_typed_steps_and_system_instruction(self) -> None:
        system, steps = to_gemini_input(self.history)
        self.assertEqual(system, "sys")
        self.assertEqual(
            [s["type"] for s in steps],
            ["user_input", "function_call", "function_call", "function_result", "function_result"],
        )
        _, plain = to_gemini_input([{"role": "user", "content": "hi"}])
        self.assertEqual(plain, [{"type": "user_input", "content": [{"type": "text", "text": "hi"}]}])

    def test_responses_replays_native_output_for_same_provider(self) -> None:
        native = [
            {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc"},
            {"type": "reasoning", "id": "rs_2"},
            {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "inspect_schema", "arguments": "{}"},
        ]
        history = list(self.history)
        history[2] = {**history[2], "provider_state": {"provider": "openai", "continuation": {"output": native}}}
        items = to_responses_input(history, "openai")
        self.assertIn({"type": "reasoning", "id": "rs_1", "encrypted_content": "enc"}, items)
        self.assertNotIn({"type": "reasoning", "id": "rs_2"}, items)
        foreign = to_responses_input(history, "xai")
        self.assertFalse(any(i.get("type") == "reasoning" for i in foreign))

    def test_anthropic_sanitizes_foreign_ids(self) -> None:
        history = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "functions.run_query_ir:0", "name": "run_query_ir", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "functions.run_query_ir:0", "name": "run_query_ir", "content": "{}"},
        ]
        _, msgs = to_anthropic(history)
        self.assertEqual(msgs[1]["content"][0]["id"], "functions_run_query_ir_0")
        self.assertEqual(msgs[2]["content"][0]["tool_use_id"], "functions_run_query_ir_0")

    def test_parse_arguments_tolerates_bad_json(self) -> None:
        self.assertEqual(parse_arguments('{"x": 1}'), {"x": 1})
        self.assertIn("_invalid_arguments", parse_arguments("{bad"))
        self.assertEqual(parse_arguments(None), {})


class TestToolLoop(unittest.TestCase):
    def setUp(self) -> None:
        self._agent_runtime = os.environ.get("AGENT_RUNTIME")
        os.environ["AGENT_RUNTIME"] = "legacy"

    def tearDown(self) -> None:
        if self._agent_runtime is None:
            os.environ.pop("AGENT_RUNTIME", None)
        else:
            os.environ["AGENT_RUNTIME"] = self._agent_runtime

    def test_evidence_records_the_currency_of_summed_money_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            snapshot = _publish_orders(c)
            snapshot["table_details"][0]["columns"][1]["currency"] = "INR"
            box = AnalyticsToolbox(
                engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()), snapshot=snapshot, semantics=None, budget=ToolBudget(4, 2)
            )
            counted = {**QUERY, "metrics": [*QUERY["metrics"], {"alias": "orders", "func": "count", "field_id": "amount"}]}
            out = box.execute(ToolCallResult("c", "run_query_ir", counted))
            self.assertFalse(out.is_error, out.content)
            self.assertEqual(out.evidence["currencies"], {"total": "INR"})
            snapshot["table_details"][0]["columns"][1] = {"name": "amount", "type": "double", "nullable": True}
            plain = box.execute(ToolCallResult("c", "run_query_ir", {**QUERY, "purpose": "Totals again"}))
            self.assertEqual(plain.evidence["currencies"], {})

    def test_toolbox_runs_query_and_returns_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            snapshot = _publish_orders(c)
            box = AnalyticsToolbox(
                engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()),
                snapshot=snapshot,
                semantics=None,
                budget=ToolBudget(4, 2),
            )
            out = box.execute(ToolCallResult("c", "run_query_ir", QUERY))
            self.assertFalse(out.is_error, out.content)
            self.assertTrue(out.evidence["evidence_id"].startswith("ev_"))
            self.assertEqual(out.evidence["currencies"], {})
            self.assertIn("north", out.content)
            bad = box.execute(ToolCallResult("c", "run_query_ir", {**QUERY, "table_id": "nope"}))
            self.assertTrue(bad.is_error)
            self.assertIn("not counted", bad.content)
            self.assertFalse(box.execute(ToolCallResult("c", "run_query_ir", QUERY)).is_error)
            self.assertTrue(box.execute(ToolCallResult("c", "run_query_ir", QUERY)).is_error)

    def test_identical_failed_calls_are_not_run_again(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            box = AnalyticsToolbox(
                engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()),
                snapshot=_publish_orders(c),
                semantics=None,
                budget=ToolBudget(10, 10),
            )
            bad = {**QUERY, "table_id": "nope"}
            first = box.execute(ToolCallResult("c1", "run_query_ir", bad))
            used = box.budget.used
            again = box.execute(ToolCallResult("c2", "run_query_ir", dict(bad)))
            self.assertTrue(first.is_error and again.is_error)
            self.assertIn("already failed", again.content)
            self.assertEqual(box.budget.used, used)
            self.assertFalse(box.execute(ToolCallResult("c3", "run_query_ir", QUERY)).is_error)
            unknown = box.execute(ToolCallResult("c4", "drop_table", {}))
            self.assertIn("calculate", unknown.content)

    def test_specialist_loop_executes_tools_and_cites_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gw = ToolCallingGateway()
            c.providers.register(gw)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            snapshot = _publish_orders(c)
            events = []
            ans = InvestigationOrchestrator(c).execute_local(
                run_id="run_tools",
                workspace_id="ws1",
                question="Which region has the most sales?",
                mode=RunMode.QUICK,
                snapshot_id="snap1",
                snapshot=snapshot,
                emit_event=events.append,
            )
            tool_events = [e for e in events if e.type.value == "tool.result"]
            self.assertEqual([e.payload["tool"] for e in tool_events], ["inspect_schema", "run_query_ir"])
            self.assertTrue(all(e.payload["ok"] for e in tool_events))
            self.assertIn("2 tool results", ans.summary_markdown)
            ev = tool_events[1].payload["evidence_id"]
            self.assertEqual(ans.claims[0].evidence_ids, (ev,))
            self.assertEqual(len(ans.charts), 1)
            chart = ans.charts[0]
            self.assertEqual((chart.chart_type, chart.evidence_id, chart.x_field), ("bar", ev, "region"))
            self.assertEqual(chart.rows, (("north", 12.5), ("south", 5.0)))
            (usage,) = c.budget_ledger.get_model_usage(["run_tools"])
            self.assertEqual((usage["provider_id"], usage["calls"], usage["unsettled_calls"]), ("openai", 2, 0))
            self.assertIsNotNone(usage["avg_latency_ms"])
            second = gw.requests[1]
            assistant = [m for m in second.messages if m.get("role") == "assistant"][0]
            self.assertEqual(assistant["provider_state"]["continuation"], {"note": "x"})

    def test_final_round_disables_tools_and_model_is_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gw = AlwaysToolsGateway()
            c.providers.register(gw)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            snapshot = _publish_orders(c)
            ans = InvestigationOrchestrator(c).execute_local(
                run_id="run_final",
                workspace_id="ws1",
                question="q",
                mode=RunMode.QUICK,
                snapshot_id="snap1",
                snapshot=snapshot,
            )
            self.assertIn("Final answer.", ans.summary_markdown)
            self.assertEqual(gw.requests[-1].tool_choice, "none")
            self.assertTrue(all(r.tool_choice is None for r in gw.requests[:-1]))
            first_model = gw.requests[0].model_id
            self.assertTrue(all(r.model_id for r in gw.requests[1:]))
            self.assertEqual(gw.requests[0].max_output_tokens, 12_000)
            self.assertEqual(first_model, "")

    def test_low_token_budget_forces_an_answer_round(self) -> None:
        from decimal import Decimal
        from app.domain.limits import QUICK_BUDGET_LIMITS

        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gw = AlwaysToolsGateway()
            c.providers.register(gw)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            c.budget_ledger.configure_run(
                "run_tokens",
                max_attempts=QUICK_BUDGET_LIMITS.total_model_attempts,
                max_tokens=QUICK_BUDGET_LIMITS.total_tokens,
                finalization_reserve_tokens=QUICK_BUDGET_LIMITS.finalization_reserve_tokens,
            )
            seed = QUICK_BUDGET_LIMITS.total_tokens - QUICK_BUDGET_LIMITS.finalization_reserve_tokens - 20_000
            c.budget_ledger.reserve("run_tokens", "seed", "seed", seed, Decimal("0"), False, None)
            os.environ["AGENT_RUNTIME"] = "deepagents"
            ans = InvestigationOrchestrator(c).execute_local(
                run_id="run_tokens", workspace_id="ws1", question="q", mode=RunMode.QUICK,
                snapshot_id="snap1", snapshot=_publish_orders(c),
            )
            specialist = [r for r in gw.requests if r.role.value == "data_specialist"]
            self.assertTrue(specialist)
            self.assertTrue(all(r.tool_choice == "none" for r in specialist))
            self.assertIn("Final answer.", ans.summary_markdown)

    def test_time_budget_stops_run(self) -> None:
        import app.application.services.orchestration_service as orch_mod

        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            c.providers.register(ToolCallingGateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events = []
            real_monotonic = orch_mod.time.monotonic
            planned = lambda: any(e.type.value == "plan.updated" for e in events)  # noqa: E731
            orch_mod.time.monotonic = lambda: real_monotonic() + 10_000 if planned() else real_monotonic()
            try:
                InvestigationOrchestrator(c).execute_local(
                    run_id="run_slow", workspace_id="ws1", question="q", mode=RunMode.QUICK, emit_event=events.append
                )
            finally:
                orch_mod.time.monotonic = real_monotonic
            finished = [e for e in events if e.type.value == "run.finished"][0].payload
            self.assertEqual(finished["status"], "failed")
            self.assertIn("Time budget", finished["error"])

    def test_older_tool_results_are_trimmed(self) -> None:
        import json as _json
        from app.application.services.orchestration_service import _compact_older_results

        big = _json.dumps({"evidence_id": "ev_x", "rows": [[i, "x" * 200] for i in range(50)], "row_count": 50})
        msgs = [{"role": "tool", "tool_call_id": "c1", "name": "run_query_ir", "content": big}]
        _compact_older_results(msgs, 1)
        data = _json.loads(msgs[0]["content"])
        self.assertEqual(len(data["rows"]), 10)
        self.assertEqual(data["evidence_id"], "ev_x")
        self.assertTrue(data["older_result_trimmed"])

    def test_evidence_verdict_flags_uncited_claims_once(self) -> None:
        from app.application.services.orchestration_service import _evidence_verdict
        from app.domain.models import Claim

        def claim(cid: str, ev: tuple[str, ...]) -> Claim:
            return Claim(claim_id=cid, label="x", statement="Revenue was $1,200.", value=None, unit=None, currency=None, evidence_ids=ev)

        flagged: set[str] = set()
        claims = [claim("clm_t1_analyze", ()), claim("clm_t1_reconcile", ("ev_1",)), claim("clm_t3_remediate", ())]
        verdict = _evidence_verdict(claims, flagged, {"clm_t3_remediate"})
        self.assertFalse(verdict.approved)
        self.assertEqual(len(verdict.gaps), 1)
        self.assertTrue(_evidence_verdict(claims, flagged, {"clm_t3_remediate"}).approved)
        sneaky = [claim("clm_t1_review_refunds", ())]
        self.assertFalse(_evidence_verdict(sneaky, set(), set()).approved)

    def _toolbox(self, c, budget=(20, 20)) -> AnalyticsToolbox:
        return AnalyticsToolbox(
            engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()),
            snapshot=_publish_orders(c),
            semantics=None,
            budget=ToolBudget(*budget),
        )

    def test_distribution_histogram_and_unique_evidence_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            box = self._toolbox(build_container(artifact_root=td))
            hist = box.execute(
                ToolCallResult("c", "run_query_ir", {"operation": "distribution", "purpose": "spread", "table_id": "orders", "field_id": "amount", "buckets": 2})
            )
            self.assertFalse(hist.is_error, hist.content)
            self.assertIn("bucket", hist.evidence["columns"])
            self.assertEqual(sum(r[hist.evidence["columns"].index("row_count")] for r in hist.evidence["rows"]), 3)
            a = box.execute(ToolCallResult("c", "run_query_ir", QUERY))
            b = box.execute(ToolCallResult("c", "run_query_ir", {**QUERY, "purpose": "same result, different purpose"}))
            self.assertEqual(a.evidence["result_hash"], b.evidence["result_hash"])
            self.assertNotEqual(a.evidence["evidence_id"], b.evidence["evidence_id"])

    def test_reconciliation_applies_filters(self) -> None:
        from app.adapters.query.snapshot_engine import table_schemas
        from app.domain.query_ir import parse_query_ir as parse

        with tempfile.TemporaryDirectory() as td:
            snap = _publish_orders(build_container(artifact_root=td))
            ir = parse({
                "operation": "reconciliation_lookup", "purpose": "x",
                "left_table_id": "orders", "right_table_id": "orders",
                "left_key": "region", "right_key": "region",
                "left_amount_field": "amount", "right_amount_field": "amount",
                "filters": [{"field_id": "region", "op": "eq", "value": "north"}],
            })
            compiled = SqlGlotQueryCompiler().compile(ir, table_schemas(snap), 100)
            self.assertEqual(compiled.sql.count("WHERE"), 2)
            self.assertEqual(compiled.parameters, ("north", "north"))

    def test_tool_calls_per_turn_are_capped_and_evidence_is_streamed(self) -> None:
        class ManyCalls(ToolCallingGateway):
            def invoke(self, request, provider_profile, model_profile, api_key):
                self.requests.append(request)
                if any(m.get("role") == "tool" for m in request.messages):
                    return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="done")
                calls = tuple(ToolCallResult(f"c{i}", "inspect_schema", {}) for i in range(10))
                return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=calls)

        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gw = ManyCalls()
            c.providers.register(gw)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events, sink = [], []
            InvestigationOrchestrator(c).execute_local(
                run_id="run_many", workspace_id="ws1", question="q", mode=RunMode.QUICK,
                snapshot_id="snap1", snapshot=_publish_orders(c), emit_event=events.append, on_evidence=sink.append,
            )
            tool_events = [e.payload for e in events if e.type.value == "tool.result"]
            self.assertEqual(len(tool_events), 10)
            self.assertEqual(sum(1 for p in tool_events if p["ok"]), 8)
            self.assertEqual(sink, [])
            seqs = [e.sequence for e in events]
            self.assertEqual(seqs, sorted(seqs))

    def test_token_estimate_counts_provider_state_and_tools(self) -> None:
        from app.adapters.orchestration.deep_agents_bridge import estimate_input_tokens
        from app.application.services.analytics_tools import RUN_QUERY_TOOL

        plain = ({"role": "user", "content": "q"},)
        with_state = plain + (
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "inspect_schema", "arguments": {}}],
                "provider_state": {"provider": "openai", "continuation": {"output": [{"type": "reasoning", "encrypted_content": "x" * 30_000}]}},
            },
        )
        self.assertGreaterEqual(estimate_input_tokens(with_state), 10_000)
        self.assertGreater(estimate_input_tokens(plain, (RUN_QUERY_TOOL,)), estimate_input_tokens(plain))

    def test_table_artifact_ids_are_path_safe(self) -> None:
        from app.application.services.ingestion import table_artifact_id

        aid = table_artifact_id("art1", "../weird/name\\x")
        self.assertNotIn("/", aid)
        self.assertNotIn("\\", aid)
        self.assertNotIn("..", aid)

    def test_detail_rows_are_withheld_from_the_model_by_default(self) -> None:
        detail = {"operation": "detail", "purpose": "list orders", "table_id": "orders", "select_fields": ["region", "amount"]}
        with tempfile.TemporaryDirectory() as td:
            box = self._toolbox(build_container(artifact_root=td))
            out = box.execute(ToolCallResult("c", "run_query_ir", detail))
            payload = json.loads(out.content)
            self.assertEqual(payload["rows"], [])
            self.assertTrue(payload["rows_withheld"])
            self.assertIn(payload["evidence_id"], payload["note"])
            self.assertEqual(payload["row_count"], 3)
            self.assertEqual(len(out.evidence["rows"]), 3)
            totals = json.loads(box.execute(ToolCallResult("c", "run_query_ir", QUERY)).content)
            self.assertTrue(totals["rows"])
            self.assertNotIn("rows_withheld", totals)

    def test_detail_rows_reach_the_model_when_every_provider_allows_it(self) -> None:
        detail = {"operation": "detail", "purpose": "list orders", "table_id": "orders", "select_fields": ["region"]}
        with tempfile.TemporaryDirectory() as td:
            box = self._toolbox(build_container(artifact_root=td))
            box.raw_record_egress = True
            payload = json.loads(box.execute(ToolCallResult("c", "run_query_ir", detail)).content)
            self.assertEqual(len(payload["rows"]), 3)
            self.assertNotIn("rows_withheld", payload)

    def test_raw_record_egress_needs_every_allowed_provider_to_permit_it(self) -> None:
        import dataclasses
        from app.application.services.orchestration_service import InvestigationOrchestrator

        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            orch = InvestigationOrchestrator(c)
            policy = c.default_routing_policy
            self.assertFalse(orch._raw_record_egress_allowed(policy))
            c.provider_profiles[ProviderId.OPENAI] = dataclasses.replace(
                c.provider_profiles[ProviderId.OPENAI], allow_raw_record_egress=True
            )
            self.assertFalse(orch._raw_record_egress_allowed(policy))
            only_openai = dataclasses.replace(policy, allowed_providers=(ProviderId.OPENAI,))
            self.assertTrue(orch._raw_record_egress_allowed(only_openai))
            self.assertFalse(orch._raw_record_egress_allowed(dataclasses.replace(policy, allowed_providers=())))

    def _wide_toolbox(self, tables: int, columns: int, name_len: int) -> AnalyticsToolbox:
        snapshot = {
            "snapshot_id": "snap_wide",
            "table_details": [
                {
                    "name": f"sheet_{t}",
                    "row_count": 10,
                    "columns": [{"name": f"c{i}_" + "x" * name_len, "type": "string"} for i in range(columns)],
                }
                for t in range(tables)
            ],
        }
        return AnalyticsToolbox(engine=None, snapshot=snapshot, semantics=None, budget=ToolBudget(20, 20))

    def test_wide_schema_is_compacted_before_columns_are_dropped(self) -> None:
        payload = json.loads(self._wide_toolbox(4, 200, 40).execute(ToolCallResult("c", "inspect_schema", {})).content)
        self.assertTrue(payload["schema_compacted"])
        self.assertEqual(len(payload["tables"][0]["columns"]), 200)

    def test_very_wide_schema_lists_tables_and_can_be_inspected_per_table(self) -> None:
        box = self._wide_toolbox(20, 200, 40)
        listed = json.loads(box.execute(ToolCallResult("c", "inspect_schema", {})).content)
        self.assertEqual(len(listed["tables"]), 20)
        self.assertEqual(listed["tables"][0]["column_count"], 200)
        self.assertIn("table_id", listed["note"])
        one = json.loads(box.execute(ToolCallResult("c", "inspect_schema", {"table_id": "sheet_3"})).content)
        self.assertEqual([t["table_id"] for t in one["tables"]], ["sheet_3"])
        self.assertEqual(len(one["tables"][0]["columns"]), 200)
        self.assertTrue(box.execute(ToolCallResult("c", "inspect_schema", {"table_id": "nope"})).is_error)

    def _calc(self, box, operation, values):
        return box.execute(ToolCallResult("c", "calculate", {"operation": operation, "purpose": "derive", "values": values}))

    def test_calculate_derives_traceable_figures_from_evidence(self) -> None:
        by_region = {"operation": "aggregate", "purpose": "totals", "table_id": "orders",
                     "metrics": [{"alias": "total", "func": "sum", "field_id": "amount"}], "group_by": ["region"],
                     "order_by": [{"field_id": "region", "direction": "asc"}]}
        with tempfile.TemporaryDirectory() as td:
            box = self._toolbox(build_container(artifact_root=td))
            ev = box.execute(ToolCallResult("c", "run_query_ir", by_region)).evidence
            rows = dict(ev["rows"])
            self.assertEqual(rows, {"north": 12.5, "south": 5.0})

            def ref(row: int) -> dict:
                return {"evidence_id": ev["evidence_id"], "row": row, "column": "total"}

            change = self._calc(box, "percent_change", [ref(0), ref(1)])
            self.assertFalse(change.is_error, change.content)
            self.assertEqual(json.loads(change.content)["result"], "150")
            self.assertEqual(change.evidence["inputs"][0]["value"], "12.5")
            self.assertIn(ev["evidence_id"], change.evidence["sql"])
            self.assertEqual(json.loads(self._calc(box, "sum", [ref(0), ref(1)]).content)["result"], "17.5")
            self.assertEqual(json.loads(self._calc(box, "difference", [ref(0), ref(1)]).content)["result"], "7.5")
            chained = {"evidence_id": change.evidence["evidence_id"], "row": 0, "column": "result"}
            self.assertEqual(json.loads(self._calc(box, "ratio", [chained, ref(1)]).content)["result"], "30")

    def test_calculate_refuses_unsafe_or_invalid_inputs(self) -> None:
        detail = {"operation": "detail", "purpose": "rows", "table_id": "orders", "select_fields": ["region", "amount"]}
        with tempfile.TemporaryDirectory() as td:
            box = self._toolbox(build_container(artifact_root=td))
            withheld = box.execute(ToolCallResult("c", "run_query_ir", detail)).evidence["evidence_id"]
            totals = box.execute(ToolCallResult("c", "run_query_ir", QUERY)).evidence
            cols = totals["columns"]
            text_col = next(c for c in cols if c == "region")
            num_col = next(c for c in cols if c != "region")
            cases = [
                ("sum", [{"evidence_id": withheld, "row": 0, "column": "amount"}]),
                ("sum", [{"evidence_id": "ev_000000000000", "row": 0, "column": num_col}]),
                ("sum", [{"evidence_id": totals["evidence_id"], "row": 0, "column": text_col}]),
                ("ratio", [{"evidence_id": totals["evidence_id"], "row": 0, "column": num_col}]),
                ("sum", [{"evidence_id": totals["evidence_id"], "row": 99, "column": num_col}]),
            ]
            for operation, values in cases:
                self.assertTrue(self._calc(box, operation, values).is_error, (operation, values))
            zero = {"operation": "aggregate", "purpose": "zero", "table_id": "orders",
                    "metrics": [{"alias": "z", "func": "sum", "field_id": "amount"}],
                    "filters": [{"field_id": "region", "op": "eq", "value": "none"}]}
            zero_ev = box.execute(ToolCallResult("c", "run_query_ir", zero)).evidence["evidence_id"]
            ref = {"evidence_id": totals["evidence_id"], "row": 0, "column": num_col}
            out = self._calc(box, "ratio", [ref, {"evidence_id": zero_ev, "row": 0, "column": "z"}])
            self.assertTrue(out.is_error)

    def test_no_snapshot_offers_no_tools(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gw = ToolCallingGateway()
            c.providers.register(gw)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            InvestigationOrchestrator(c).execute_local(
                run_id="run_nodata", workspace_id="ws1", question="q", mode=RunMode.QUICK
            )
            self.assertEqual(gw.requests[0].tools, ())
            self.assertEqual(len(gw.requests), 1)


if __name__ == "__main__":
    unittest.main()
