import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

from app.domain.errors import ValidationError

MAX_CA_PEM_BYTES = 64_000
CA_PEM_KEY = "ca_pem"


def ca_pem_from(credentials: dict[str, str]) -> str | None:
    pem = (credentials.get(CA_PEM_KEY) or "").strip()
    if not pem:
        return None
    if len(pem) > MAX_CA_PEM_BYTES or "-----BEGIN CERTIFICATE-----" not in pem:
        raise ValidationError(
            "The CA certificate must be PEM text starting with -----BEGIN CERTIFICATE-----.",
            remediation="Paste your provider's CA certificate (or bundle) as shown in its download.",
        )
    return pem


@contextmanager
def ca_file(pem: str) -> Iterator[str]:
    handle = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
    try:
        with handle:
            handle.write(pem)
        yield handle.name
    finally:
        os.unlink(handle.name)
