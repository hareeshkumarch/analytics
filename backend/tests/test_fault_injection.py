import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, pool
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.orm_models import Base
from app.adapters.persistence.pg_repositories import PgEventLog, PgRunRepository
from app.api.main import create_app
from app.application.ports.providers import ToolCallResult
from app.application.services.analytics_tools import TOOL_SPECS, AnalyticsToolbox, ToolBudget
from app.bootstrap.container import build_container
from app.domain.events import EventType
from app.domain.providers import ProviderId
from app.jobs import runner
from tests.test_run_fencing import _Gateway
from tests.test_tool_loop import QUERY, ToolCallingGateway, _publish_orders


def _commit_lost(*args, **kwargs):
    raise OperationalError("COMMIT", {}, Exception("server closed the connection unexpectedly"))


class TestCommitLostWhileSavingTheAnswer(unittest.TestCase):
    def test_api_run_reports_one_failed_finish_and_never_completed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            container.providers.register(ToolCallingGateway())
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            app = create_app(container)
            client = TestClient(app)
            app.state.repo_runs.save_answer = _commit_lost
            run_id = client.post("/api/v1/workspaces/ws1/runs", json={"question": "q", "mode": "quick", "execute_inline": True}).json()["run_id"]
            finishes = [e for e in app.state.repo_events.list_after(run_id, 0) if e.type == EventType.RUN_FINISHED]
            self.assertEqual([e.payload["status"] for e in finishes], ["failed"])
            self.assertEqual(client.get(f"/api/v1/workspaces/ws1/runs/{run_id}").json()["status"], "failed")

    def test_worker_leaves_the_run_reclaimable_and_a_redelivery_finishes_it_once(self) -> None:
        sf = create_session_factory(create_db_engine())
        runs, events = PgRunRepository(sf), PgEventLog(sf)
        runs.create_run(run_id="run_c", workspace_id="ws1", conversation_id="c", mode="quick", status="queued", question="Fees?")
        artifacts = tempfile.TemporaryDirectory()
        self.addCleanup(artifacts.cleanup)

        def build():
            c = build_container(artifact_root=artifacts.name)
            c.providers.register(_Gateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            return c

        with patch.object(runner, "get_worker_session_factory", return_value=sf), patch.object(runner, "build_container", build):
            with patch.object(PgRunRepository, "save_answer", _commit_lost), self.assertRaises(OperationalError):
                runner.execute_persisted_run("run_c")
            self.assertEqual(runs.get_run("run_c")["status"], "running")
            self.assertFalse([e for e in events.list_after("run_c", 0) if e.type == EventType.RUN_FINISHED])
            result = runner.execute_persisted_run("run_c")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(runs.get_run("run_c")["answer"]["summary_markdown"])
        finishes = [e for e in events.list_after("run_c", 0) if e.type == EventType.RUN_FINISHED]
        self.assertEqual([e.payload["status"] for e in finishes], ["completed"])


class TestPoolExhaustion(unittest.TestCase):
    def test_requests_fail_fast_with_503_and_connections_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine = create_engine(
                f"sqlite:///{Path(td) / 'pool.db'}",
                connect_args={"check_same_thread": False},
                poolclass=pool.QueuePool,
                pool_size=1,
                max_overflow=0,
                pool_timeout=0.5,
            )
            Base.metadata.create_all(engine)
            client = TestClient(create_app(build_container(artifact_root=td), session_factory=sessionmaker(bind=engine)))
            self.assertEqual(client.get("/api/v1/workspaces/ws1/runs").status_code, 200)
            held = engine.connect()
            started = time.monotonic()
            busy = client.get("/api/v1/workspaces/ws1/runs")
            self.assertEqual(busy.status_code, 503)
            self.assertLess(time.monotonic() - started, 5)
            held.close()
            self.assertEqual(client.get("/api/v1/workspaces/ws1/runs").status_code, 200)
            self.assertEqual(engine.pool.checkedout(), 0)

    def test_production_pool_waits_a_bounded_time(self) -> None:
        with patch.dict("os.environ", {"TESTING": "0", "DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:1/db"}):
            engine = create_db_engine()
        self.assertEqual(engine.pool.timeout(), 5)
        self.assertEqual((engine.pool.size(), engine.pool._max_overflow), (10, 20))


class TestOversizedToolArguments(unittest.TestCase):
    def test_huge_arguments_are_refused_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            box = AnalyticsToolbox(engine=None, snapshot=_publish_orders(c), semantics=None, budget=ToolBudget(5, 5))
            limit = TOOL_SPECS["run_query_ir"].max_argument_bytes
            out = box.execute(ToolCallResult("c", "run_query_ir", {**QUERY, "purpose": "x" * (limit + 1)}))
            payload = json.loads(out.content)
            self.assertEqual((payload["status"], payload["code"]), ("denied", "LIMIT_EXCEEDED"))
            self.assertEqual(box.budget.used, 0)


if __name__ == "__main__":
    unittest.main()
