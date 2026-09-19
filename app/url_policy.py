"""Validation and SSRF policy for configured Pi-hole instance URLs."""
from __future__ import annotations

from dataclasses import dataclass
import functools
import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit

PUBLIC_ALLOWLIST_ENV = "PIHOLE_MANAGER_PUBLIC_ALLOWLIST"
_PRIVATE_V4 = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_PRIVATE_V6 = (ipaddress.ip_network("fc00::/7"),)
_METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(value)
    for value in ("169.254.169.254", "100.100.100.200", "fd00:ec2::254")
)


class URLPolicyError(ValueError):
    """The configured instance URL is not permitted by the outbound policy."""


@dataclass(frozen=True)
class ValidatedDestination:
    url: str
    host: str
    port: int | None
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]


@dataclass(frozen=True)
class PublicAllowlist:
    hostnames: frozenset[str]
    addresses: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]

    @classmethod
    def from_environment(cls) -> "PublicAllowlist":
        hostnames: set[str] = set()
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for raw in os.environ.get(PUBLIC_ALLOWLIST_ENV, "").split(","):
            entry = raw.strip()
            if not entry:
                continue
            try:
                if "/" in entry:
                    networks.append(ipaddress.ip_network(entry, strict=False))
                else:
                    addresses.add(ipaddress.ip_address(entry))
                continue
            except ValueError:
                pass
            hostnames.add(_normalize_hostname(entry, field="allowlist hostname"))
        return cls(frozenset(hostnames), frozenset(addresses), tuple(networks))

    def permits_address(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return address in self.addresses or any(
            address.version == network.version and address in network for network in self.networks
        )


@functools.lru_cache(maxsize=1)
def get_public_allowlist() -> PublicAllowlist:
    # issue #10: parse the allowlist once, not on every PiholeClient construction
    return PublicAllowlist.from_environment()


def _normalize_hostname(value: str, *, field: str = "hostname") -> str:
    value = value.rstrip(".").lower()
    if not value or "%" in value or any(char.isspace() or ord(char) < 32 for char in value):
        raise URLPolicyError(f"Invalid {field}")
    try:
        ascii_value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise URLPolicyError(f"Invalid {field}") from exc
    if len(ascii_value) > 253 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(char.isalnum() or char == "-" for char in label)
        for label in ascii_value.split(".")
    ):
        raise URLPolicyError(f"Invalid {field}")
    return ascii_value


def normalize_instance_url(value: str) -> str:
    """Return a canonical origin-only HTTP(S) URL without resolving DNS."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise URLPolicyError("Instance URL must not be empty or have surrounding whitespace")
    if "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise URLPolicyError("Instance URL contains an ambiguous or control character")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise URLPolicyError("Instance URL is malformed or has an invalid port") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise URLPolicyError("Instance URL scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise URLPolicyError("Instance URL must not contain embedded credentials")
    if not parsed.hostname:
        raise URLPolicyError("Instance URL must contain a hostname")
    if parsed.path not in {"", "/"}:
        raise URLPolicyError("Instance URL must not contain a path")
    if parsed.query or parsed.fragment:
        raise URLPolicyError("Instance URL must not contain a query string or fragment")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        rendered_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    except ValueError:
        rendered_host = _normalize_hostname(parsed.hostname)
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def _effective_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address):
    return address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address


def _prohibited(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    address = _effective_address(address)
    return (
        address in _METADATA_ADDRESSES
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def _prohibited_literal_host(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # A block list URL is fetched by Pi-hole, never by this process, so there is no
    # SSRF surface here — only an obviously-wrong literal destination to refuse.
    return _prohibited(address)


def _default_private(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    address = _effective_address(address)
    return any(address in network for network in (_PRIVATE_V4 if address.version == 4 else _PRIVATE_V6))


def _screen_addresses(
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    *,
    hostname: str,
    allowlist: PublicAllowlist,
) -> None:
    hostname_allowed = hostname in allowlist.hostnames
    for original in addresses:
        address = _effective_address(original)
        if _prohibited(address):
            raise URLPolicyError(f"Instance resolves to prohibited address {address}")
        if not _default_private(address) and not hostname_allowed and not allowlist.permits_address(address):
            raise URLPolicyError(f"Public instance address {address} is not allowlisted")


def resolve_instance_destination(
    value: str, *, allowlist: PublicAllowlist | None = None, resolver=socket.getaddrinfo
) -> ValidatedDestination:
    """Resolve and validate every current DNS answer before connecting."""
    normalized = normalize_instance_url(value)
    parsed = urlsplit(normalized)
    assert parsed.hostname is not None
    allowlist = allowlist or get_public_allowlist()
    try:
        addresses = {ipaddress.ip_address(parsed.hostname)}
    except ValueError:
        try:
            answers = resolver(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise URLPolicyError(f"Instance hostname could not be resolved: {exc}") from exc
        addresses = {ipaddress.ip_address(answer[4][0]) for answer in answers}
        if not addresses:
            raise URLPolicyError("Instance hostname did not resolve to an address")
    _screen_addresses(addresses, hostname=parsed.hostname, allowlist=allowlist)
    return ValidatedDestination(
        normalized,
        parsed.hostname,
        parsed.port,
        tuple(sorted(addresses, key=str)),
    )


def validate_instance_destination(
    value: str, *, allowlist: PublicAllowlist | None = None, resolver=socket.getaddrinfo
) -> str:
    return resolve_instance_destination(value, allowlist=allowlist, resolver=resolver).url


def screen_adlist_url(value: str) -> str:
    # issue #4: syntax and canonicalization only (plan 0012); Pi-hole fetches the URL.
    if value != value.strip() or "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise URLPolicyError("Block list URL contains invalid characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise URLPolicyError("Block list URL is malformed") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise URLPolicyError("Block list URL must be a complete HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise URLPolicyError("Block list URL must not contain embedded credentials")
    if parsed.fragment:
        raise URLPolicyError("Block list URL must not contain a fragment")
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
        host = address.compressed
        rendered_host = f"[{host}]" if address.version == 6 else host
    except ValueError:
        rendered_host = host
        address = None
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    path = "" if parsed.path == "/" else parsed.path
    canonical = urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))
    if address is not None and _prohibited_literal_host(address):
        raise URLPolicyError(f"Block list URL points at prohibited address {address}")
    return canonical
