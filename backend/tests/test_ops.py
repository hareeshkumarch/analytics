import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.orm_models import BudgetReservationRow
from app.adapters.persistence.pg_repositories import PgOpsMetrics, PgOpsSwitches, PgRunRepository
from app.api.main import create_app
from app.application.services.ops_controls import OpsSwitches
from app.application.services.ops_metrics import render_prometheus
from app.bootstrap.container import build_container
from app.domain.errors import ServicePausedError, ValidationError
from app.domain.providers import ProviderId
from tests.test_run_fencing import _Gateway


class TestOpsSwitches(unittest.TestCase):
    def setUp(self) -> None:
        self.store = PgOpsSwitches(create_session_factory(create_db_engine()))
        self.now = [0.0]
        self.switches = OpsSwitches(self.store, clock=lambda: self.now[0])

    def test_pausing_runs_and_providers_is_recorded_and_reversible(self) -> None:
        self.switches.ensure_runs_allowed()
        self.switches.set("runs", True, "Provider outage")
        with self.assertRaises(ServicePausedError) as raised:
            self.switches.ensure_runs_allowed()
        self.assertIn("Provider outage", str(raised.exception))
        self.switches.set("provider:openai", True, "Rate limited")
        self.assertEqual(self.switches.paused_providers(), frozenset({ProviderId.OPENAI}))
        self.switches.set("runs", False, "Recovered")
        self.switches.ensure_runs_allowed()
        self.assertEqual([h["paused"] for h in self.switches.history(10)], [False, True, True])
        self.assertEqual({s["switch_id"]: s["paused"] for s in self.switches.list_all()}, {"provider:openai": True, "runs": False})
        with self.assertRaises(ValidationError):
            self.switches.set("provider:nope", True, "x")

    def test_other_processes_see_changes_after_the_cache_expires(self) -> None:
        other = OpsSwitches(self.store, clock=lambda: self.now[0])
        other.ensure_runs_allowed()
        self.switches.set("runs", True, "Maintenance")
        other.ensure_runs_allowed()
        self.now[0] = 6.0
        with self.assertRaises(ServicePausedError):
            other.ensure_runs_allowed()


class TestMetricsSnapshot(unittest.TestCase):
    def test_counts_queue_stuck_runs_and_model_call_errors(self) -> None:
        sf = create_session_factory(create_db_engine())
        runs = PgRunRepository(sf)
        for run_id in ("run_q", "run_r"):
            runs.create_run(run_id=run_id, workspace_id="ws1", conversation_id=f"c_{run_id}", mode="quick", status="queued", question="Fees?")
        runs.claim_run("run_r")
        with sf() as db, db.begin():
            for i, reason in enumerate((None, None, "timeout")):
                db.add(BudgetReservationRow(reservation_id=f"r{i}", run_id="run_r", operation_id="op", attempt_id=f"a{i}", reserved_tokens=10, provider_id="openai", unresolved_reason=reason))
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        snap = PgOpsMetrics(sf).snapshot(now=later, latency_window_seconds=3600, stuck_after_seconds=1200)
        self.assertEqual(snap["stuck_runs"], 1)
        self.assertGreater(snap["queue_oldest_age_seconds"], 7000)
        self.assertEqual({(c["provider"], c["result"]): c["count"] for c in snap["model_calls"]}, {("openai", "ok"): 2, ("openai", "error"): 1})
        self.assertEqual(snap["outbox_pending"], 0)
        text = render_prometheus(snap, {"runs": "Maintenance"}, 3600)
        self.assertIn('ledgerline_model_calls_total{provider="openai",result="error"} 1', text)
        self.assertIn("ledgerline_queue_depth 1", text)
        self.assertIn("ledgerline_stuck_runs 1", text)
        self.assertIn('ledgerline_switch_paused{switch="runs"} 1', text)

    def test_render_quantiles_ratio_and_label_escaping(self) -> None:
        snap = {
            "runs": [{"status": "completed", "mode": "quick", "outcome": 'odd"one', "count": 3}],
            "recent_finished": [{"mode": "quick", "outcome": "success"}] * 3 + [{"mode": "quick", "outcome": "needs_review"}],
            "durations": {"quick": [1.0, 2.0, 3.0, 40.0]},
            "queue_oldest_age_seconds": 0.0,
            "stuck_runs": 0,
            "model_calls": [],
            "outbox_pending": 0,
        }
        text = render_prometheus(snap, {}, 3600)
        self.assertIn('outcome="odd\\"one"', text)
        self.assertIn("ledgerline_verified_completion_ratio 0.75", text)
        self.assertIn('ledgerline_run_duration_seconds{mode="quick",quantile="0.5"} 2', text)
        self.assertIn('ledgerline_run_duration_seconds{mode="quick",quantile="0.95"} 40', text)


class TestOpsApi(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        c = build_container(artifact_root=self._td.name)
        c.providers.register(_Gateway())
        c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
        self.client = TestClient(create_app(c))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _ask(self):
        return self.client.post("/api/v1/workspaces/ws1/runs", json={"question": "Fees?", "execute_inline": True})

    def test_ready_metrics_and_switches(self) -> None:
        ready = self.client.get("/api/v1/ready")
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertEqual(ready.json()["checks"], {"database": "ok"})

        res = self.client.put("/api/v1/ops/switches/runs", json={"paused": True, "reason": "Incident 42"})
        self.assertEqual(res.status_code, 200, res.text)
        refused = self._ask()
        self.assertEqual(refused.status_code, 503)
        self.assertEqual(refused.json()["code"], "service_paused")
        self.assertIn("Incident 42", refused.json()["message"])
        self.client.put("/api/v1/ops/switches/runs", json={"paused": False, "reason": "Resolved"})
        self.assertEqual(self._ask().status_code, 202)

        self.client.put("/api/v1/ops/switches/provider:openai", json={"paused": True, "reason": "Outage"})
        run_id = self._ask().json()["run_id"]
        run = self.client.get(f"/api/v1/workspaces/ws1/runs/{run_id}").json()
        self.assertEqual(run["status"], "failed")
        self.assertIn("paused providers: openai", str(run.get("error")))

        listed = self.client.get("/api/v1/ops/switches").json()
        self.assertIn("provider:anthropic", listed["available"])
        self.assertEqual(len(listed["history"]), 3)
        self.assertEqual(self.client.put("/api/v1/ops/switches/everything", json={"paused": True, "reason": "x"}).status_code, 422)

        metrics = self.client.get("/api/v1/metrics")
        self.assertEqual(metrics.status_code, 200)
        self.assertIn('ledgerline_runs{status="completed",mode="quick"', metrics.text)
        self.assertIn('ledgerline_runs{status="failed"', metrics.text)
        self.assertIn('ledgerline_switch_paused{switch="provider:openai"} 1', metrics.text)
        self.assertIn('ledgerline_run_duration_seconds{mode="quick",quantile="0.95"}', metrics.text)


if __name__ == "__main__":
    unittest.main()
