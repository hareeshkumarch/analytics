import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.adapters.query.duckdb_executor import DuckDbQueryExecutor
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.ports.query import CompiledQuery
from app.application.services.analytics_tools import RUN_QUERY_TOOL, TOOL_SPECS, ToolEffect, args_fingerprint
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.errors import QueryTimeoutError
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from tests.test_tool_loop import QUERY, ToolCallingGateway, _publish_orders

BAD = {"operation": "aggregate", "purpose": "broken"}


class MixedGateway(ToolCallingGateway):
    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.requests.append(request)
        names = {t.name for t in request.tools or ()}
        tool_msgs = [m for m in request.messages if m.get("role") == "tool"]
        if "run_query_ir" in names and not tool_msgs:
            calls = (
                ToolCallResult("a", "inspect_schema", {}),
                ToolCallResult("b", "run_query_ir", QUERY),
                ToolCallResult("c", "run_query_ir", BAD),
            )
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=calls)
        if "run_query_ir" in names and len(tool_msgs) == 3:
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=(ToolCallResult("d", "run_query_ir", BAD),))
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="North leads.")


def _run(container, run_id: str = "run_tex") -> list:
    container.providers.register(MixedGateway())
    container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
    events: list = []
    InvestigationOrchestrator(container).execute_local(
        run_id=run_id, workspace_id="ws1", question="Which region?", mode=RunMode.QUICK,
        snapshot_id="snap1", snapshot=_publish_orders(container), emit_event=events.append,
    )
    return [e.payload for e in events if e.type.value == "tool.result"]


class TestToolSpecs(unittest.TestCase):
    def test_every_tool_declares_its_contract(self) -> None:
        for name, spec in TOOL_SPECS.items():
            self.assertEqual(spec.definition.name, name)
            self.assertTrue(spec.version)
            self.assertIn(spec.effect, (ToolEffect.READ, ToolEffect.COMPUTE))
            self.assertGreater(spec.timeout_seconds, 0)
            self.assertGreater(spec.max_inline_bytes, 0)


class TestToolExecutionLedger(unittest.TestCase):
    def test_every_call_is_recorded_once_with_status_and_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            results = _run(c)
            rows = c.tool_executions.list_for_runs(["run_tex"])
        self.assertEqual(len(rows), len(results))
        got = sorted((r.tool, r.status, r.error_code or "") for r in rows)
        self.assertEqual(
            got,
            sorted([
                ("inspect_schema", "success", ""),
                ("run_query_ir", "success", ""),
                ("run_query_ir", "failed", "INVALID_ARGUMENT"),
                ("run_query_ir", "denied", "DUPLICATE_FAILED_CALL"),
            ]),
        )
        by_status = {r.status: r for r in rows if r.tool == "run_query_ir"}
        self.assertEqual(by_status["success"].args_fingerprint, args_fingerprint("run_query_ir", QUERY))
        self.assertEqual(by_status["failed"].args_fingerprint, by_status["denied"].args_fingerprint)
        self.assertEqual(by_status["failed"].args_fingerprint, args_fingerprint("run_query_ir", BAD))
        self.assertTrue((by_status["success"].evidence_id or "").startswith("ev_"))
        self.assertTrue(all(r.tool_version == "1" and r.task_id and r.duration_ms >= 0 for r in rows))

    def test_timeout_is_passed_to_the_engine_and_recorded(self) -> None:
        seen: list[int] = []

        def slow(self, snapshot, ir, semantic_version_id, max_rows, *, timeout_seconds):
            seen.append(timeout_seconds)
            raise QueryTimeoutError("Query exceeded execution deadline")

        with tempfile.TemporaryDirectory() as td, patch.object(SnapshotQueryEngine, "run", slow):
            c = build_container(artifact_root=td)
            _run(c)
            rows = c.tool_executions.list_for_runs(["run_tex"])
        self.assertEqual(seen[0], TOOL_SPECS[RUN_QUERY_TOOL.name].timeout_seconds)
        self.assertIn(("failed", "TIMEOUT"), [(r.status, r.error_code) for r in rows if r.tool == "run_query_ir"])

    def test_executor_stops_a_query_at_its_deadline(self) -> None:
        sql = "SELECT count(*) FROM range(200000000) a, range(1000) b WHERE (a.range + b.range) % 97 = 3"
        compiled = CompiledQuery(sql, (), "h", "v", "aggregate", "slow", 10)
        started = time.monotonic()
        with self.assertRaises(QueryTimeoutError):
            DuckDbQueryExecutor().execute_tables(compiled, {}, snapshot_id="s", semantic_version_id="v", timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 10)

    def test_durable_ledger_endpoint_and_usage_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            client = TestClient(app)
            run_id = client.post("/api/v1/workspaces/ws1/runs", json={"question": "q", "background_async": False}).json()["run_id"]
            c = app.state.container
            c.providers.register(MixedGateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            InvestigationOrchestrator(c).execute_local(
                run_id=run_id, workspace_id="ws1", question="Which region?", mode=RunMode.QUICK,
                snapshot_id="snap1", snapshot=_publish_orders(c),
            )
            listed = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}/tool-executions")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()["executions"]), 4)
            self.assertEqual(client.get(f"/api/v1/workspaces/ws2/runs/{run_id}/tool-executions").status_code, 404)
            tools = {t["tool"]: t for t in client.get("/api/v1/workspaces/ws1/usage").json()["tools"]}
            self.assertEqual(tools["run_query_ir"]["calls"], 3)
            self.assertEqual((tools["run_query_ir"]["invalid_calls"], tools["run_query_ir"]["duplicate_calls"]), (1, 1))
            self.assertEqual(tools["run_query_ir"]["failed_calls"], 2)


if __name__ == "__main__":
    unittest.main()
