import os
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.adapters.persistence.crypto import CredentialCipher
from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.pg_repositories import PgCredentialRepository
from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.errors import SecurityPolicyError
from app.jobs.rotate_credentials import rotate_credentials


class TestKeyRotation(unittest.TestCase):
    def setUp(self) -> None:
        self.old_key = Fernet.generate_key().decode()
        self.new_key = Fernet.generate_key().decode()

    def test_listed_keys_decrypt_and_rotation_moves_to_the_newest(self) -> None:
        old_token = CredentialCipher(self.old_key).encrypt("sk-secret")
        both = CredentialCipher(f"{self.new_key},{self.old_key}")
        self.assertEqual(both.decrypt(old_token), "sk-secret")
        repo = PgCredentialRepository(create_session_factory(create_db_engine("sqlite:///:memory:")))
        repo.save_credential("ws1", "openai", old_token)
        self.assertEqual(rotate_credentials(repo, both), (1, 0))
        rotated = repo.list_active_credentials("ws1")[0]["encrypted_payload"]
        self.assertEqual(CredentialCipher(self.new_key).decrypt(rotated), "sk-secret")
        with self.assertRaises(SecurityPolicyError):
            CredentialCipher(self.old_key).decrypt(rotated)

    def test_undecryptable_key_is_reported_as_needing_a_key(self) -> None:
        sf = create_session_factory(create_db_engine("sqlite:///:memory:"))
        PgCredentialRepository(sf).save_credential("ws1", "openai", CredentialCipher(self.old_key).encrypt("sk-lost"))
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"APP_MASTER_KEY": self.new_key}):
            client = TestClient(create_app(build_container(artifact_root=td), session_factory=sf))
            providers = {p["provider_id"]: p for p in client.get("/api/v1/workspaces/ws1/providers").json()["providers"]}
            self.assertEqual(providers["openai"]["key_status"], "needs_key")
            self.assertFalse(providers["openai"]["configured"])
            self.assertEqual(providers["groq"]["key_status"], "missing")
            client.put("/api/v1/workspaces/ws1/settings/provider-key", json={"provider_id": "openai", "api_key": "sk-new"})
            providers = {p["provider_id"]: p for p in client.get("/api/v1/workspaces/ws1/providers").json()["providers"]}
            self.assertEqual(providers["openai"]["key_status"], "ready")


if __name__ == "__main__":
    unittest.main()
