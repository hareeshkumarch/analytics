import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.persistence.crypto import CredentialCipher
from app.api.main import create_app, validate_loopback_bind
from app.bootstrap.container import build_container
from app.domain.errors import SecurityPolicyError


class TestApiAndInfra(unittest.TestCase):
    def test_loopback_bind_rejects_public_host(self) -> None:
        validate_loopback_bind("127.0.0.1")
        with self.assertRaises(SecurityPolicyError):
            validate_loopback_bind("0.0.0.0")

    def test_fastapi_health_providers_and_run_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            app = create_app(container)
            with TestClient(app) as client:
                r_health = client.get("/api/v1/health")
                self.assertEqual(r_health.status_code, 200)
                body = r_health.json()
                for family in (
                    "uploaded_file",
                    "uploaded_sqlite",
                    "uploaded_duckdb",
                    "remote_file",
                    "postgresql",
                    "mysql",
                    "google_sheets",
                    "rest_json",
                    "airtable",
                    "bigquery",
                ):
                    self.assertIn(family, body["connectors"])
                self.assertEqual(len(body["providers"]), 7)

                r_prov = client.get("/api/v1/workspaces/ws1/providers")
                self.assertEqual(r_prov.status_code, 200)
                self.assertEqual(len(r_prov.json()["providers"]), 7)

                r1 = client.post(
                    "/api/v1/workspaces/ws1/runs",
                    headers={"Idempotency-Key": "idem-1"},
                    json={"question": "Reconcile INR orders", "mode": "quick"},
                )
                self.assertEqual(r1.status_code, 202)
                run_id = r1.json()["run_id"]

                r2 = client.post(
                    "/api/v1/workspaces/ws1/runs",
                    headers={"Idempotency-Key": "idem-1"},
                    json={"question": "Reconcile INR orders", "mode": "quick"},
                )
                self.assertEqual(r2.json()["run_id"], run_id)

                r3 = client.post(
                    "/api/v1/workspaces/ws1/runs",
                    headers={"Idempotency-Key": "idem-1"},
                    json={"question": "Different question", "mode": "deep"},
                )
                self.assertEqual(r3.status_code, 409)

                r_sse = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}/events?after=0")
                self.assertEqual(r_sse.status_code, 200)
                self.assertIn("run.status", r_sse.text)

    def test_credential_cipher_requires_master_key(self) -> None:
        with self.assertRaises(SecurityPolicyError):
            CredentialCipher(master_key="")
        key = Fernet.generate_key().decode("utf-8")
        cipher = CredentialCipher(master_key=key)
        enc = cipher.encrypt("secret-api-key")
        self.assertNotIn("secret-api-key", enc)
        self.assertEqual(cipher.decrypt(enc), "secret-api-key")


if __name__ == "__main__":
    unittest.main()
