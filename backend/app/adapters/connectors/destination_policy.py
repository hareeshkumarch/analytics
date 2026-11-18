import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from app.domain.errors import SecurityPolicyError

_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


@dataclass(frozen=True, slots=True)
class DestinationPolicy:
    allowed_hosts: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = ()
    allow_private_local_for_tests: bool = False
    require_https: bool = True


def validate_ip_address(ip_str: str, *, allow_private_local: bool) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(ip_str)
    if ip in _METADATA_IPS or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise SecurityPolicyError(f"Destination IP {ip} is forbidden (metadata/link-local/multicast)")
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return validate_ip_address(str(ip.ipv4_mapped), allow_private_local=allow_private_local)
    if (ip.is_private or ip.is_loopback or ip.is_reserved) and not allow_private_local:
        raise SecurityPolicyError(f"Private or loopback destination IP {ip} is not allowlisted")
    return ip


def validate_and_resolve_host(
    host: str,
    port: int,
    policy: DestinationPolicy,
) -> str:
    if not host:
        raise SecurityPolicyError("Destination host cannot be empty")
    if policy.allowed_hosts and host.lower() not in {h.lower() for h in policy.allowed_hosts}:
        raise SecurityPolicyError(f"Host {host!r} is not in operator allowlist")
    if policy.allowed_ports and port not in policy.allowed_ports:
        raise SecurityPolicyError(f"Port {port} is not in operator allowlist")

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SecurityPolicyError(f"DNS resolution failed for {host!r}: {exc}") from exc

    if not infos:
        raise SecurityPolicyError(f"No addresses resolved for {host!r}")

    validated_ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        ip_obj = validate_ip_address(addr, allow_private_local=policy.allow_private_local_for_tests)
        validated_ips.append(str(ip_obj))
    return validated_ips[0]


def validate_http_url(url: str, policy: DestinationPolicy) -> tuple[str, str, int, str]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if policy.require_https and scheme != "https":
        raise SecurityPolicyError(f"HTTPS is required by destination policy (got {scheme!r})")
    if scheme not in ("http", "https"):
        raise SecurityPolicyError(f"Unsupported URL scheme: {scheme!r}")
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    resolved_ip = validate_and_resolve_host(host, port, policy)
    origin = f"{scheme}://{host}:{port}"
    return origin, host, port, resolved_ip
