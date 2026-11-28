import base64
import json
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient

from app.adapters.query.duckdb_executor import DuckDbQueryExecutor
from app.adapters.query.result_cache import SingleFlightCache
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.api.main import create_app
from app.application.ports.providers import ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget
from app.bootstrap.container import build_container
from app.domain.errors import QueryTimeoutError
from app.domain.query_ir import parse_query_ir
from tests.test_tool_loop import QUERY, _publish_orders


class CountingExecutor(DuckDbQueryExecutor):
    def __init__(self, delay: float = 0.0) -> None:
        super().__init__()
        self.calls = 0
        self.delay = delay
        self.fail = False
        self._count_lock = threading.Lock()

    def execute_tables(self, *args, **kwargs):
        with self._count_lock:
            self.calls += 1
        time.sleep(self.delay)
        if self.fail:
            raise QueryTimeoutError("Query exceeded execution deadline")
        return super().execute_tables(*args, **kwargs)


class TestQueryResultCache(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.container = build_container(artifact_root=self._td.name)
        self.snapshot = _publish_orders(self.container)
        self.executor = CountingExecutor(delay=0.2)
        self.engine = SnapshotQueryEngine(
            self.container.artifacts, SqlGlotQueryCompiler(), executor=self.executor, result_cache=SingleFlightCache()
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run(self, ir: dict, *, snapshot: dict | None = None, semantic: str = "sem_1", tz: str = "UTC"):
        return self.engine.run(snapshot or self.snapshot, parse_query_ir(ir), semantic, 1000, business_timezone=tz)[0]

    def test_parallel_identical_queries_execute_once(self) -> None:
        results = []
        threads = [threading.Thread(target=lambda: results.append(self._run(QUERY))) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.executor.calls, 1)
        self.assertEqual(len({r.result_hash for r in results}), 1)
        self.assertEqual(sorted(r.cache_hit for r in results), [False, True, True, True])

    def test_other_scope_never_reuses_a_result(self) -> None:
        self._run(QUERY)
        self._run(QUERY, semantic="sem_2")
        self._run(QUERY, tz="Asia/Kolkata")
        self._run(QUERY, snapshot={**self.snapshot, "snapshot_id": "snap_other"})
        self._run({**QUERY, "filters": [{"field_id": "region", "op": "eq", "value": "north"}]})
        self.assertEqual(self.executor.calls, 5)
        self._run({**QUERY, "purpose": "same query, different reason"})
        self.assertEqual(self.executor.calls, 5)

    def test_hits_keep_per_query_evidence_and_are_recorded(self) -> None:
        box = AnalyticsToolbox(engine=self.engine, snapshot=self.snapshot, semantics=None, budget=ToolBudget(10, 10))
        first = box.execute(ToolCallResult("a", "run_query_ir", QUERY))
        # The first call runs the query and the data-policy check on its grouping column; both are then cached.
        after_first = self.executor.calls
        again = box.execute(ToolCallResult("b", "run_query_ir", {**QUERY, "purpose": "again"}))
        self.assertEqual(self.executor.calls, after_first)
        self.assertEqual((first.summary["cache_hit"], again.summary["cache_hit"]), (False, True))
        self.assertTrue(again.evidence["cache_hit"])
        self.assertEqual(first.evidence["result_hash"], again.evidence["result_hash"])
        self.assertNotEqual(first.evidence["evidence_id"], again.evidence["evidence_id"])
        self.assertEqual(json_rows(first.content), json_rows(again.content))

    def test_failures_are_not_cached(self) -> None:
        self.executor.fail = True
        for _ in range(2):
            with self.assertRaises(QueryTimeoutError):
                self._run(QUERY)
        self.executor.fail = False
        self.assertFalse(self._run(QUERY).cache_hit)
        self.assertEqual(self.executor.calls, 3)


def json_rows(content: str) -> list:
    return json.loads(content)["rows"]


class TestDeletingADatasetDropsItsResults(unittest.TestCase):
    def test_delete_invalidates_cached_results(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            client = TestClient(app)
            csv = base64.b64encode(b"region,amount\nnorth,10\n").decode("ascii")
            sid = client.post(
                "/api/v1/workspaces/ws1/connectors/extract",
                json={"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "raw_base64": csv}, "selected_tables": ["orders"]},
            ).json()["snapshot"]["snapshot_id"]
            snapshot = app.state.repo_snapshots.get_snapshot(sid)
            executor = CountingExecutor()
            engine = SnapshotQueryEngine(app.state.container.artifacts, SqlGlotQueryCompiler(), executor=executor)
            for _ in range(2):
                engine.run(snapshot, parse_query_ir(QUERY), "", 1000)
            self.assertEqual(executor.calls, 1)
            self.assertEqual(client.delete(f"/api/v1/workspaces/ws1/snapshots/{sid}?force=true").status_code, 200)
            engine.run(snapshot, parse_query_ir(QUERY), "", 1000)
            self.assertEqual(executor.calls, 2)


class TestCacheHitsAreRecorded(unittest.TestCase):
    def test_a_repeated_question_is_served_from_cache_and_counted(self) -> None:
        from app.application.ports.tools import summarize_tool_executions
        from app.application.services.orchestration_service import InvestigationOrchestrator
        from app.domain.models import RunMode
        from app.domain.providers import ProviderId
        from tests.test_tool_loop import ToolCallingGateway

        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            c.providers.register(ToolCallingGateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            snapshot = {**_publish_orders(c), "snapshot_id": "snap_cache_ledger"}
            for run_id in ("run_first", "run_again"):
                InvestigationOrchestrator(c).execute_local(
                    run_id=run_id, workspace_id="ws1", question="Which region?", mode=RunMode.QUICK, snapshot_id="snap_cache_ledger", snapshot=snapshot
                )
            hits = {r.run_id: r.cache_hit for r in c.tool_executions.list_for_runs(["run_first", "run_again"]) if r.tool == "run_query_ir"}
            self.assertEqual(hits, {"run_first": False, "run_again": True})
            (query_stats,) = [t for t in summarize_tool_executions(c.tool_executions.list_for_runs(["run_first", "run_again"])) if t["tool"] == "run_query_ir"]
            self.assertEqual(query_stats["cache_hits"], 1)


if __name__ == "__main__":
    unittest.main()
