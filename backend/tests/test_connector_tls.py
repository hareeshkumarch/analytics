import os
import ssl
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.adapters.connectors.mysql_connector import _tls_context
from app.adapters.connectors.postgres_connector import PostgresConnector
from app.adapters.connectors.tls import ca_pem_from
from app.application.ports.clock import SystemClock
from app.domain.errors import ValidationError


def _ca_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Provider CA")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


class TestCaCertificateInput(unittest.TestCase):
    def test_validation(self) -> None:
        self.assertIsNone(ca_pem_from({}))
        self.assertIsNone(ca_pem_from({"ca_pem": "  "}))
        with self.assertRaises(ValidationError):
            ca_pem_from({"ca_pem": "not a certificate"})
        with self.assertRaises(ValidationError):
            ca_pem_from({"ca_pem": "-----BEGIN CERTIFICATE-----" + "A" * 70_000})

    def test_mysql_verify_full_trusts_only_the_pasted_ca(self) -> None:
        pinned = _tls_context("verify-full", _ca_pem())
        assert pinned is not None
        subjects = [dict(x[0] for x in cert["subject"]) for cert in pinned.get_ca_certs()]
        self.assertEqual(subjects, [{"commonName": "Test Provider CA"}])
        self.assertTrue(pinned.check_hostname)
        default = _tls_context("verify-full")
        assert default is not None
        self.assertGreater(len(default.get_ca_certs()), 1)
        relaxed = _tls_context("require", _ca_pem())
        assert relaxed is not None
        self.assertEqual(relaxed.verify_mode, ssl.CERT_NONE)


class TestPostgresRootCertificate(unittest.TestCase):
    def _connect_kwargs(self, sslmode: str, host: str = "localhost", **creds: str) -> dict:
        seen: dict = {}

        def fake_connect(**kwargs):
            seen.update(kwargs)
            root = kwargs.get("sslrootcert")
            seen["file_existed"] = bool(root and root != "system" and os.path.exists(root))
            return SimpleNamespace()

        config = {"host": host, "port": 5432, "database": "db", "sslmode": sslmode}
        with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=fake_connect)}):
            PostgresConnector(SystemClock())._connect(config, {"username": "u", "password": "p", **creds})
        return seen

    def test_root_certificate_choice(self) -> None:
        self.assertEqual(self._connect_kwargs("verify-full")["sslrootcert"], "system")
        self.assertNotIn("sslrootcert", self._connect_kwargs("verify-ca"))
        self.assertNotIn("sslrootcert", self._connect_kwargs("require", ca_pem=_ca_pem()))
        pinned = self._connect_kwargs("verify-ca", ca_pem=_ca_pem())
        self.assertTrue(pinned["file_existed"])
        self.assertFalse(os.path.exists(pinned["sslrootcert"]))

    def test_public_hosts_get_tls_by_default_and_refuse_plaintext(self) -> None:
        with patch("app.adapters.connectors.postgres_connector.validate_and_resolve_host", return_value="93.184.216.34"):
            self.assertEqual(self._connect_kwargs("", host="db.example.com")["sslmode"], "require")
            for mode in ("disable", "allow", "prefer"):
                with self.assertRaises(ValidationError):
                    self._connect_kwargs(mode, host="db.example.com")
        self.assertEqual(self._connect_kwargs("")["sslmode"], "prefer")
        self.assertEqual(self._connect_kwargs("disable")["sslmode"], "disable")


class TestTlsModeForHost(unittest.TestCase):
    def test_mysql_rules(self) -> None:
        from app.adapters.connectors.tls import tls_mode_for

        rule = {"plaintext_modes": ("disable",), "local_default": "disable"}
        self.assertEqual(tls_mode_for("10.0.0.5", "", **rule), "disable")
        self.assertEqual(tls_mode_for("127.0.0.1", "disable", **rule), "disable")
        self.assertEqual(tls_mode_for("93.184.216.34", "", **rule), "require")
        self.assertEqual(tls_mode_for("93.184.216.34", "verify-full", **rule), "verify-full")
        with self.assertRaises(ValidationError):
            tls_mode_for("93.184.216.34", "disable", **rule)


if __name__ == "__main__":
    unittest.main()
