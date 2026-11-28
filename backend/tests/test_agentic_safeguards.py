"""Safeguards of the default agent runtime: wind-down, retries, cancellation, data policy and partial work."""

import dataclasses
import json
import tempfile
import threading
import time
import unittest

import pyarrow as pa
import pyarrow.ipc as pa_ipc
from fastapi.testclient import TestClient

from app.adapters.providers.http_utils import classify_http_error
from app.adapters.providers.langchain_gateway import GatewayChatModel
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.errors import ProviderError
from app.domain.limits import MAX_TOOL_ROUNDS, QUICK_BUDGET_LIMITS, WIND_DOWN_SECONDS
from app.domain.models import RunMode
from app.domain.providers import AgentRole, ModelProfile, ProviderId, ProviderProfile
from tests.test_tool_loop import QUERY, AlwaysToolsGateway, ToolCallingGateway, _publish_orders

B = "/api/v1/workspaces/ws1"


def _container(td: str, gateway):
    c = build_container(artifact_root=td)
    c.providers.register(gateway)
    c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
    return c


class TestWindDown(unittest.TestCase):
    def test_specialist_answers_before_the_run_is_out_of_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            gateway = AlwaysToolsGateway()
            c = _container(td, gateway)
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_attempts", workspace_id="ws1", question="q", mode=RunMode.QUICK,
                snapshot_id="snap1", snapshot=_publish_orders(c),
            )
            specialist = [r for r in gateway.requests if r.role == AgentRole.DATA_SPECIALIST]
            # A model that never stops calling tools would otherwise spend every call the run has
            # (6 in Quick mode) and fail on the next; it is told to answer while one call is still left.
            self.assertEqual(specialist[-1].tool_choice, "none")
            self.assertLess(len(specialist), MAX_TOOL_ROUNDS + 1)
            used = int(c.budget_ledger.get_run_totals("run_attempts")["used_attempts"])
            self.assertLessEqual(used, QUICK_BUDGET_LIMITS.total_model_attempts)
            self.assertIn("Final answer.", answer.summary_markdown)

    def test_low_attempts_or_little_time_left_ends_tool_use(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _container(td, ToolCallingGateway())

            def model(**signals) -> GatewayChatModel:
                return GatewayChatModel(
                    routing_service=c.routing_service, policy=c.default_routing_policy, run_id="r", role=AgentRole.DATA_SPECIALIST, **signals
                )

            self.assertFalse(model()._must_wind_down())
            self.assertFalse(model(attempts_left=lambda: 3, seconds_left=lambda: WIND_DOWN_SECONDS + 30)._must_wind_down())
            self.assertTrue(model(attempts_left=lambda: 2)._must_wind_down())
            self.assertTrue(model(seconds_left=lambda: WIND_DOWN_SECONDS - 1)._must_wind_down())
            self.assertFalse(model(seconds_left=lambda: None)._must_wind_down())


class _FlakyGateway:
    """Rate-limited a fixed number of times, then answers."""

    provider_id = ProviderId.OPENAI

    def __init__(self, failures: int, retry_after: str | None) -> None:
        self.failures = failures
        self.retry_after = retry_after
        self.calls = 0

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise classify_http_error(429, "slow down", "OpenAI", self.retry_after)
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="ok", input_tokens=5, output_tokens=2)


class TestRateLimitRetry(unittest.TestCase):
    def _step(self, gateway, *, fallback: bool) -> tuple[ModelResponse | None, list[float], Exception | None]:
        with tempfile.TemporaryDirectory() as td:
            c = _container(td, gateway)
            waits: list[float] = []
            c.routing_service._sleep = waits.append  # type: ignore[method-assign]
            policy = dataclasses.replace(c.default_routing_policy, fallback_enabled=fallback)
            request = ModelRequest(
                request_id="req1", run_id="run_retry", role=AgentRole.DATA_SPECIALIST, model_id="",
                messages=({"role": "user", "content": "hi"},),
            )
            try:
                return c.routing_service.execute_step(request=request, policy=policy, estimated_input_tokens=10), waits, None
            except Exception as exc:  # noqa: BLE001
                return None, waits, exc

    def test_the_only_model_is_retried_after_the_wait_the_provider_asked_for(self) -> None:
        gateway = _FlakyGateway(failures=2, retry_after="7")
        response, waits, error = self._step(gateway, fallback=False)
        self.assertIsNone(error)
        self.assertEqual(response.text, "ok")  # type: ignore[union-attr]
        self.assertEqual((gateway.calls, waits), (3, [7.0, 7.0]))

    def test_without_a_retry_after_header_the_wait_grows(self) -> None:
        gateway = _FlakyGateway(failures=2, retry_after=None)
        _, waits, error = self._step(gateway, fallback=False)
        self.assertIsNone(error)
        self.assertEqual(len(waits), 2)
        self.assertTrue(1.0 <= waits[0] < 1.5 and 2.0 <= waits[1] < 2.5, waits)

    def test_retries_are_bounded(self) -> None:
        gateway = _FlakyGateway(failures=99, retry_after="1")
        response, waits, error = self._step(gateway, fallback=False)
        self.assertIsNone(response)
        self.assertIsInstance(error, ProviderError)
        self.assertEqual((gateway.calls, len(waits)), (3, 2))

    def test_retry_after_is_carried_on_the_error(self) -> None:
        self.assertEqual(classify_http_error(429, "", "OpenAI", "12").retry_after, 12.0)
        self.assertIsNone(classify_http_error(503, "", "OpenAI").retry_after)
        self.assertIsNone(classify_http_error(400, "", "OpenAI", "12").retry_after)


class _BlockingGateway:
    """The first model call waits until released, so a cancel can arrive while the run is in flight."""

    provider_id = ProviderId.OPENAI

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            self.release.wait(15)
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="Done.", input_tokens=5, output_tokens=2)


class TestCancelReachesARunningInlineRun(unittest.TestCase):
    def test_no_model_call_is_made_after_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            gateway = _BlockingGateway()
            app = create_app(_container(td, gateway))
            with TestClient(app) as client:
                run_id = client.post(f"{B}/runs", json={"question": "anything", "mode": "deep", "background_async": True}).json()["run_id"]
                self.assertTrue(gateway.entered.wait(10), "the run never reached its first model call")
                # A read replaces the in-memory run record, which is what used to hide the cancel from the loop.
                self.assertEqual(client.get(f"{B}/runs/{run_id}").json()["status"], "running")
                self.assertEqual(client.post(f"{B}/runs/{run_id}/cancel").json()["status"], "cancelled")
                gateway.release.set()
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and run_id in app.state.bg_tasks:
                    time.sleep(0.05)
                self.assertNotIn(run_id, app.state.bg_tasks, "the cancelled run is still executing")
                self.assertEqual(gateway.calls, 1)
                self.assertEqual(client.get(f"{B}/runs/{run_id}").json()["status"], "cancelled")


def _publish_customers(container) -> dict:
    emails = [f"person{i}@example.com" for i in range(12)]
    table = pa.table({"email": emails, "region": ["north", "south"] * 6, "amount": [float(i) for i in range(12)]})
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    staged = container.artifacts.stage_bytes("job_c", "customers.arrow", sink.getvalue().to_pybytes(), "application/octet-stream")
    container.artifacts.publish_staged("job_c", staged, "art_c__customers")
    return {
        "snapshot_id": "snap_c",
        "table_artifacts": {"customers": "art_c__customers"},
        "table_details": [
            {
                "name": "customers",
                "row_count": 12,
                "columns": [
                    {"name": "email", "type": "string", "nullable": True},
                    {"name": "region", "type": "string", "nullable": True},
                    {"name": "amount", "type": "double", "nullable": True},
                ],
            }
        ],
    }


class TestRowLevelDataPolicy(unittest.TestCase):
    def _run(self, box: AnalyticsToolbox, **query) -> dict:
        base = {"operation": "aggregate", "purpose": "p", "table_id": "customers", "metrics": [{"alias": "total", "func": "sum", "field_id": "amount"}]}
        return json.loads(box.execute(ToolCallResult("c", "run_query_ir", {**base, **query})).content)

    def _box(self, td: str) -> AnalyticsToolbox:
        c = build_container(artifact_root=td)
        return AnalyticsToolbox(
            engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()), snapshot=_publish_customers(c), semantics=None, budget=ToolBudget(20, 20)
        )

    def test_grouping_by_an_identifier_is_withheld_like_a_detail_query(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            box = self._box(td)
            by_email = self._run(box, group_by=["email"])
            self.assertEqual(by_email["rows"], [])
            self.assertTrue(by_email["rows_withheld"])
            self.assertEqual(by_email["row_count"], 12)
            self.assertNotIn("person0@example.com", json.dumps(by_email))
            self.assertIn("email", by_email["note"])
            # A filter does not make an identifier any less of one.
            filtered = self._run(box, group_by=["email"], filters=[{"field_id": "region", "op": "eq", "value": "north"}], limit=3)
            self.assertEqual(filtered["rows"], [])

    def test_real_groupings_still_reach_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            by_region = self._run(self._box(td), group_by=["region"])
            self.assertEqual(len(by_region["rows"]), 2)
            self.assertNotIn("rows_withheld", by_region)

    def test_smallest_value_of_a_text_column_is_withheld(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            box = self._box(td)
            first_email = self._run(box, metrics=[{"alias": "first", "func": "min", "field_id": "email"}])
            self.assertEqual(first_email["rows"], [])
            self.assertTrue(first_email["rows_withheld"])
            smallest_amount = self._run(box, metrics=[{"alias": "low", "func": "min", "field_id": "amount"}])
            self.assertEqual(smallest_amount["rows"], [[0.0]])

    def test_providers_allowed_to_see_raw_records_get_the_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            box = self._box(td)
            box.raw_record_egress = True
            self.assertEqual(len(self._run(box, group_by=["email"])["rows"]), 12)


class _FailsAfterQueryingGateway(ToolCallingGateway):
    """Queries the data, then the provider fails before the specialist can write its answer."""

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        has_results = any(m.get("role") == "tool" for m in request.messages)
        if request.role == AgentRole.DATA_SPECIALIST and has_results:
            self.requests.append(request)
            raise ProviderError("OpenAI rejected the request (HTTP 400)", code="provider_invalid_request", retryable=False)
        return super().invoke(request, provider_profile, model_profile, api_key)


class TestPartialWorkIsKept(unittest.TestCase):
    def test_a_step_interrupted_after_querying_keeps_its_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _container(td, _FailsAfterQueryingGateway())
            seen: list[dict] = []
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_partial", workspace_id="ws1", question="q", mode=RunMode.QUICK,
                snapshot_id="snap1", snapshot=_publish_orders(c), on_evidence=seen.append,
            )
            self.assertTrue(seen, "the query that ran before the failure produced no evidence")
            self.assertTrue(
                any("stopped before it could write a conclusion" in limitation for limitation in answer.limitations), answer.limitations
            )
            self.assertNotEqual(answer.outcome, "success")
            self.assertEqual(QUERY["purpose"], "Total amount by region")


if __name__ == "__main__":
    unittest.main()
