import os
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.api.main import create_app
from app.application.services.routing_service import routing_policy_from_saved
from app.bootstrap.container import build_container
from app.domain.providers import ProviderId


def _db_down(*args, **kwargs):
    raise OperationalError("SELECT 1", {}, Exception("connection refused"))


class TestDatabaseUrlRequired(unittest.TestCase):
    def test_missing_database_url_fails_outside_tests(self) -> None:
        with patch.dict(os.environ, {"TESTING": "0"}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            with self.assertRaises(RuntimeError) as ctx:
                create_db_engine()
        self.assertIn("DATABASE_URL", str(ctx.exception))


class TestDatabaseErrorsAreReported(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["APP_MASTER_KEY"] = Fernet.generate_key().decode("utf-8")
        self._td = tempfile.TemporaryDirectory()
        self.container = build_container(artifact_root=self._td.name)
        self.app = create_app(self.container, session_factory=create_session_factory(create_db_engine("sqlite:///:memory:")))
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _assert_unavailable(self, response) -> None:
        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()
        self.assertEqual(body["code"], "persistence_unavailable")
        self.assertTrue(body["retryable"])
        self.assertIn("PostgreSQL", body["remediation"])

    def test_reads_fail_instead_of_serving_memory(self) -> None:
        with patch.object(self.app.state.repo_runs, "list_runs", _db_down):
            self._assert_unavailable(self.client.get("/api/v1/workspaces/ws1/runs"))
            self._assert_unavailable(self.client.get("/api/v1/workspaces/ws1/usage"))
        with patch.object(self.app.state.repo_pins, "list_pins", _db_down):
            self._assert_unavailable(self.client.get("/api/v1/workspaces/ws1/pins"))
        with patch.object(self.app.state.repo_creds, "list_active_credentials", _db_down):
            self._assert_unavailable(self.client.get("/api/v1/workspaces/ws1/providers"))
        with patch.object(self.app.state.repo_routing, "get_policy", _db_down):
            self._assert_unavailable(self.client.get("/api/v1/workspaces/ws1/routing"))

    def test_failed_key_save_is_not_reported_as_configured(self) -> None:
        with patch.object(self.app.state.repo_creds, "save_credential", _db_down):
            response = self.client.put(
                "/api/v1/workspaces/ws1/settings/provider-key",
                json={"provider_id": "openai", "api_key": "sk-test-never-stored"},
            )
        self._assert_unavailable(response)
        self.assertNotEqual(self.container.routing_service._api_keys.get(ProviderId.OPENAI), "sk-test-never-stored")
        self.assertNotIn("ws1:openai", self.app.state.encrypted_keys)

    def test_failed_run_create_is_not_reported_as_queued(self) -> None:
        with patch.object(self.app.state.repo_runs, "create_run", _db_down):
            response = self.client.post("/api/v1/workspaces/ws1/runs", json={"question": "Total sales?"})
        self._assert_unavailable(response)
        self.assertEqual(self.app.state.runs, {})

    def test_failed_routing_save_is_not_reported_as_saved(self) -> None:
        with patch.object(self.app.state.repo_routing, "save_policy", _db_down):
            response = self.client.put("/api/v1/workspaces/ws1/routing", json={"expected_version": 1, "preset": "cost"})
        self._assert_unavailable(response)
        self.assertEqual(self.app.state.routing_policies["ws1"].preset.value, "balanced")

    def test_run_fails_closed_when_cost_policy_cannot_load(self) -> None:
        with patch.object(self.app.state.repo_routing, "get_policy", _db_down):
            created = self.client.post(
                "/api/v1/workspaces/ws1/runs",
                json={"question": "Total sales?", "execute_inline": True},
            )
        self.assertEqual(created.status_code, 202)
        run = self.client.get(f"/api/v1/workspaces/ws1/runs/{created.json()['run_id']}").json()
        self.assertEqual(run["status"], "failed")
        self.assertIn("cost limit", run["error"])


class TestRoutingPolicyRoundTrip(unittest.TestCase):
    def test_saved_policy_survives_restart_including_a_removed_ceiling(self) -> None:
        sf = create_session_factory(create_db_engine("sqlite:///:memory:"))
        with tempfile.TemporaryDirectory() as td:
            first = TestClient(create_app(build_container(artifact_root=td), session_factory=sf))
            saved = first.put(
                "/api/v1/workspaces/ws1/routing",
                json={"expected_version": 1, "hard_cost_ceiling_usd": "1.50"},
            )
            self.assertEqual(saved.status_code, 200)
            removed = first.put(
                "/api/v1/workspaces/ws1/routing",
                json={"expected_version": saved.json()["policy_version"], "hard_cost_ceiling_usd": None},
            )
            self.assertIsNone(removed.json()["hard_cost_ceiling_usd"])

            restarted_app = create_app(build_container(artifact_root=td), session_factory=sf)
            restarted = TestClient(restarted_app).get("/api/v1/workspaces/ws1/routing").json()
            self.assertIsNone(restarted["hard_cost_ceiling_usd"])
            self.assertEqual(restarted["policy_version"], removed.json()["policy_version"])

    def test_unsaved_workspace_uses_default_ceiling(self) -> None:
        container = build_container(artifact_root=tempfile.gettempdir())
        default = container.default_routing_policy
        self.assertIs(routing_policy_from_saved(None, default), default)
        loaded = routing_policy_from_saved({"policy_version": 3, "hard_cost_ceiling_usd": "0.75"}, default)
        self.assertEqual(loaded.hard_cost_ceiling_usd, Decimal("0.75"))
        self.assertEqual(loaded.allowed_providers, default.allowed_providers)


if __name__ == "__main__":
    unittest.main()
