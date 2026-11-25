import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.domain.errors import SecurityPolicyError


class CredentialCipher:
    def __init__(self, master_key: str | None = None) -> None:
        raw = master_key if master_key is not None else os.environ.get("APP_MASTER_KEY", "")
        keys = [part.strip() for part in raw.split(",") if part.strip()]
        if not keys:
            raise SecurityPolicyError(
                "APP_MASTER_KEY is not configured; plaintext credential persistence is forbidden.",
                remediation="Set APP_MASTER_KEY to a valid Fernet key in your local .env file.",
            )
        try:
            self._fernet = MultiFernet([Fernet(key.encode("utf-8")) for key in keys])
        except Exception as exc:
            raise SecurityPolicyError(f"Invalid APP_MASTER_KEY format: {exc}") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise SecurityPolicyError("Failed to decrypt credential with current APP_MASTER_KEY") from exc

    def rotate(self, ciphertext: str) -> str:
        try:
            return self._fernet.rotate(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise SecurityPolicyError("Failed to decrypt credential with current APP_MASTER_KEY") from exc
