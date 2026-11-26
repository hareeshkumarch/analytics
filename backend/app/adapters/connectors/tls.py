import ipaddress
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


def is_local_address(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return address.is_loopback or address.is_private


def tls_mode_for(host_ip: str, requested: str, *, plaintext_modes: tuple[str, ...], local_default: str) -> str:
    local = is_local_address(host_ip)
    mode = requested or (local_default if local else "require")
    if mode in plaintext_modes and not local:
        raise ValidationError(
            f"TLS mode {mode!r} can send credentials unencrypted to a public host.",
            remediation="Use require, or verify-full with the provider's CA certificate.",
        )
    return mode
