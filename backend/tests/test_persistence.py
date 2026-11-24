import os
import tempfile
import unittest
from decimal import Decimal

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.models import Answer, Claim
from app.domain.providers import ProviderId


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


if __name__ == "__main__":
    unittest.main()
