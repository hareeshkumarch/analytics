import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.adapters.persistence.crypto import CredentialCipher
from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.pg_repositories import PgBudgetLedger, PgRunRepository
from app.domain.errors import BudgetExhaustedError
from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.models import Answer, Claim, RunStatus
from app.domain.providers import ProviderId, RoutingPreset


class TestPersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Fernet.generate_key().decode("utf-8")
        os.environ["APP_MASTER_KEY"] = self.key

    def test_credential_persistence_and_reload_on_startup(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        sf = create_session_factory(engine)

        with tempfile.TemporaryDirectory() as td:
            container1 = build_container(artifact_root=td)
            app1 = create_app(container1, session_factory=sf)
            client1 = TestClient(app1)

            # 1. Save provider key via endpoint
            r_save = client1.put(
                "/api/v1/workspaces/ws_persist/settings/provider-key",
                json={"provider_id": "openai", "api_key": "sk-test-secret-key-12345"},
            )
            self.assertEqual(r_save.status_code, 200)
            self.assertEqual(r_save.json()["status"], "configured")
            self.assertEqual(container1.routing_service._api_keys[ProviderId.OPENAI], "sk-test-secret-key-12345")

            # Check provider list shows configured
            r_prov1 = client1.get("/api/v1/workspaces/ws_persist/providers")
            self.assertEqual(r_prov1.status_code, 200)
            openai_p = next(p for p in r_prov1.json()["providers"] if p["provider_id"] == "openai")
            self.assertTrue(openai_p["configured"])

            # 2. Simulate complete restart: new container and new app sharing the same DB session factory
            container2 = build_container(artifact_root=td)
            self.assertEqual(container2.routing_service._api_keys.get(ProviderId.OPENAI), "")

            app2 = create_app(container2, session_factory=sf)
            client2 = TestClient(app2)

            # Verification: On startup, key was automatically decrypted and loaded into container2
            self.assertEqual(container2.routing_service._api_keys[ProviderId.OPENAI], "sk-test-secret-key-12345")
            r_prov2 = client2.get("/api/v1/workspaces/ws_persist/providers")
            openai_p2 = next(p for p in r_prov2.json()["providers"] if p["provider_id"] == "openai")
            self.assertTrue(openai_p2["configured"])

    def test_routing_policy_persistence(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        sf = create_session_factory(engine)

        with tempfile.TemporaryDirectory() as td:
            container1 = build_container(artifact_root=td)
            app1 = create_app(container1, session_factory=sf)
            client1 = TestClient(app1)

            # Update routing preset
            r_put = client1.put(
                "/api/v1/workspaces/ws_persist/routing",
                json={"expected_version": 1, "preset": "latency", "fallback_enabled": False},
            )
            self.assertEqual(r_put.status_code, 200)
            self.assertEqual(r_put.json()["preset"], "latency")
            new_ver = r_put.json()["policy_version"]

            # Conflict test
            r_conf = client1.put(
                "/api/v1/workspaces/ws_persist/routing",
                json={"expected_version": 1, "preset": "cost", "fallback_enabled": True},
            )
            self.assertEqual(r_conf.status_code, 409)

            # Restart app
            container2 = build_container(artifact_root=td)
            app2 = create_app(container2, session_factory=sf)
            client2 = TestClient(app2)

            r_get = client2.get("/api/v1/workspaces/ws_persist/routing")
            self.assertEqual(r_get.status_code, 200)
            self.assertEqual(r_get.json()["preset"], "latency")
            self.assertEqual(r_get.json()["policy_version"], new_ver)
            self.assertFalse(r_get.json()["fallback_enabled"])

    def test_run_creation_listing_and_events_persistence(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        sf = create_session_factory(engine)

        with tempfile.TemporaryDirectory() as td:
            container1 = build_container(artifact_root=td)
            app1 = create_app(container1, session_factory=sf)
            client1 = TestClient(app1)

            # Create run
            r_create = client1.post(
                "/api/v1/workspaces/ws_persist/runs",
                json={"question": "What is total revenue?", "mode": "quick"},
            )
            self.assertEqual(r_create.status_code, 202)
            run_id = r_create.json()["run_id"]

            # Save an answer directly
            ans = Answer(
                answer_id="ans_test_123",
                run_id=run_id,
                summary_markdown="Revenue is 1000 INR.",
                claims=(
                    Claim(
                        claim_id="clm_1",
                        label="Total Revenue",
                        statement="Revenue is 1000 INR",
                        value=Decimal("1000"),
                        unit=None,
                        currency="INR",
                        evidence_ids=(),
                    ),
                ),
                charts=(),
                limitations=(),
                snapshot_id="snap_1",
                semantic_version_id="sem_1",
                created_at=None,
            )
            app1.state.repo_runs.save_answer(ans)

            # Restart app
            container2 = build_container(artifact_root=td)
            app2 = create_app(container2, session_factory=sf)
            client2 = TestClient(app2)

            # List runs from DB
            r_list = client2.get("/api/v1/workspaces/ws_persist/runs")
            self.assertEqual(r_list.status_code, 200)
            runs = r_list.json()["runs"]
            self.assertTrue(any(r["run_id"] == run_id for r in runs))

            # Get single run
            r_get = client2.get(f"/api/v1/workspaces/ws_persist/runs/{run_id}")
            self.assertEqual(r_get.status_code, 200)
            self.assertEqual(r_get.json()["status"], "completed")
            self.assertEqual(r_get.json()["answer"]["summary_markdown"], "Revenue is 1000 INR.")

            # Events streaming past events replay
            r_events = client2.get(f"/api/v1/workspaces/ws_persist/runs/{run_id}/events?after=0")
            self.assertEqual(r_events.status_code, 200)
            self.assertIn("run.status", r_events.text)

    def test_budget_ledger_release_frees_budget_and_config_survives(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        sf = create_session_factory(engine)
        runs = PgRunRepository(sf)
        ledger = PgBudgetLedger(sf)

        runs.create_run(
            run_id="run_bud", workspace_id="ws_bud", conversation_id="conv_bud",
            mode="quick", status="running", question="budget?",
        )
        ledger.configure_run("run_bud", max_attempts=2, max_tokens=2000, max_cost_usd=Decimal("1.00"))

        res = ledger.reserve(
            "run_bud", "op_1", "att_1",
            estimated_tokens=1000, estimated_cost_usd=Decimal("0.40"),
            cost_is_unknown=False, hard_cost_ceiling_usd=Decimal("1.00"),
        )
        totals = ledger.get_run_totals("run_bud")
        self.assertEqual(totals["used_attempts"], 1)
        self.assertEqual(totals["total_tokens"], 1000)
        self.assertEqual(Decimal(totals["total_cost_usd"]), Decimal("0.40"))

        # A second reservation would breach the configured 2000-token ceiling.
        with self.assertRaises(BudgetExhaustedError):
            ledger.reserve(
                "run_bud", "op_2", "att_2",
                estimated_tokens=1500, estimated_cost_usd=Decimal("0.10"),
                cost_is_unknown=False, hard_cost_ceiling_usd=Decimal("1.00"),
            )

        ledger.release_unresolved(res.reservation_id, "boom")
        totals = ledger.get_run_totals("run_bud")
        self.assertEqual(totals["used_attempts"], 0)
        self.assertEqual(totals["total_tokens"], 0)
        self.assertEqual(totals["total_cost_usd"], "0")
        self.assertTrue(totals["incomplete"])

        # The freed budget admits two fresh attempts that the leak would
        # have rejected (attempts 3 > 2 and cost 1.20 > 1.00).
        for i in (2, 3):
            ledger.reserve(
                "run_bud", f"op_{i}", f"att_{i}",
                estimated_tokens=1000, estimated_cost_usd=Decimal("0.40"),
                cost_is_unknown=False, hard_cost_ceiling_usd=Decimal("1.00"),
            )
        totals = ledger.get_run_totals("run_bud")
        self.assertEqual(totals["used_attempts"], 2)
        self.assertEqual(Decimal(totals["total_cost_usd"]), Decimal("0.80"))

        # The configured limits live in the database, not in the ledger
        # instance: a fresh ledger still enforces the $1.00 ceiling.
        runs.create_run(
            run_id="run_bud2", workspace_id="ws_bud", conversation_id="conv_bud",
            mode="quick", status="running", question="budget 2?",
        )
        ledger.configure_run("run_bud2", max_attempts=10, max_tokens=100_000, max_cost_usd=Decimal("1.00"))
        ledger2 = PgBudgetLedger(sf)
        ledger2.reserve(
            "run_bud2", "op_1", "att_1",
            estimated_tokens=100, estimated_cost_usd=Decimal("0.80"),
            cost_is_unknown=False, hard_cost_ceiling_usd=Decimal("1.00"),
        )
        with self.assertRaises(BudgetExhaustedError):
            ledger2.reserve(
                "run_bud2", "op_2", "att_2",
                estimated_tokens=100, estimated_cost_usd=Decimal("0.50"),
                cost_is_unknown=False, hard_cost_ceiling_usd=Decimal("1.00"),
            )


if __name__ == "__main__":
    unittest.main()
