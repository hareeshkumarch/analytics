import logging

from app.adapters.persistence.crypto import CredentialCipher
from app.adapters.persistence.database import create_session_factory
from app.adapters.persistence.pg_repositories import PgCredentialRepository
from app.domain.errors import SecurityPolicyError

logger = logging.getLogger(__name__)


def rotate_credentials(repo: PgCredentialRepository, cipher: CredentialCipher) -> tuple[int, int]:
    rotated = failed = 0
    for cred in repo.list_active_credentials():
        try:
            repo.save_credential(cred["workspace_id"], cred["provider"], cipher.rotate(cred["encrypted_payload"]))
            rotated += 1
        except SecurityPolicyError:
            logger.warning("Could not decrypt the %s key for workspace %s", cred["provider"], cred["workspace_id"])
            failed += 1
    return rotated, failed


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    rotated, failed = rotate_credentials(PgCredentialRepository(create_session_factory()), CredentialCipher())
    print(f"Re-encrypted {rotated} provider keys with the first APP_MASTER_KEY; {failed} could not be decrypted.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
