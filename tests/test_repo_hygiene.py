"""Guards the public tree against private infrastructure details."""
from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CHECKED_PRIVATE_NETS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "127.0.0.0/8", "169.254.0.0/16",
)))
ALLOWED_PRIVATE_NETS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "127.0.0.0/8", "169.254.0.0/16",
    "192.168.1.0/24", "192.168.0.0/32", "192.168.2.0/24", "192.168.10.0/24",
)))
GENERIC_HOST_SUFFIXES = (
    ".lan", ".local", ".invalid", ".test", ".example", ".arpa", ".internal",
    ".localhost",
)
ALLOWED_DOMAINS = {
    "example.com", "npmjs.org", "github.com", "githubusercontent.com",
    "jsdelivr.net", "opencollective.com", "tidelift.com", "eslint.org",
    "react.dev", "w3.org", "gitea.com", "pi.hole", "pypi.org",
}
IPV4_LITERAL = re.compile(r"(?<!\d)(?<!\d\.)(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
URL_HOST = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://(?:[^\s/@]+@)?|[\w.+-]+@)"
    r"(?P<host>\[[0-9a-f:]+\]|[a-z0-9_](?:[a-z0-9_.-]*[a-z0-9_])?)",
    re.IGNORECASE,
)


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    )
    return [os.fsdecode(name) for name in result.stdout.split(b"\0") if name]


def _tracked_texts() -> tuple[list[tuple[str, str]], list[str]]:
    """Every tracked path and its contents, plus the paths that could not be read.

    An unreadable file is reported rather than skipped. Skipping it would let content
    that is staged, and therefore published, go unscanned.
    """
    result = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=ROOT, check=True, capture_output=True,
    )
    symlinks = {
        os.fsdecode(entry.split(b"\t", 1)[1])
        for entry in result.stdout.split(b"\0")
        if entry.startswith(b"120000 ")
    }
    texts: list[tuple[str, str]] = []
    unreadable: list[str] = []
    for name in _tracked_files():
        texts.append((name, name))
        path = ROOT / name
        try:
            if name in symlinks:
                contents = os.readlink(os.fsencode(path)).decode("utf-8", errors="replace")
            else:
                contents = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            unreadable.append(f"{name}:0: unreadable tracked file: cannot be scanned")
            continue
        texts.append((name, contents))
    return texts, unreadable


def _finding(name: str, text: str, match: re.Match[str], category: str) -> str:
    line = text.count("\n", 0, match.start()) + 1
    return f"{name}:{line}: {category}: {match.group(0)}"


def test_tracked_files_use_only_approved_addresses_and_hosts() -> None:
    texts, findings = _tracked_texts()
    for name, text in texts:
        for match in IPV4_LITERAL.finditer(text):
            try:
                address = ipaddress.ip_address(match.group(0))
            except ValueError:
                continue
            if (any(address in net for net in CHECKED_PRIVATE_NETS)
                    and not any(address in net for net in ALLOWED_PRIVATE_NETS)):
                findings.append(_finding(name, text, match, "private IPv4 address"))
        for match in URL_HOST.finditer(text):
            host = match.group("host").lower().rstrip(".")
            try:
                ipaddress.ip_address(host.strip("[]"))
            except ValueError:
                approved = (
                    "." not in host
                    or host == "192.168.1.x"
                    or host.endswith(GENERIC_HOST_SUFFIXES)
                    or any(host == domain or host.endswith("." + domain)
                           for domain in ALLOWED_DOMAINS)
                )
                if not approved:
                    findings.append(_finding(name, text, match, "unapproved host"))
    assert not findings, "\n" + "\n".join(findings)


def test_tracked_files_carry_no_known_private_tokens() -> None:
    path = ROOT / ".private-denylist.toml"
    if not path.exists():
        message = f"{path.name} is absent"
        if os.environ.get("FILTERHELM_REQUIRE_DENYLIST") == "1":
            pytest.fail(message)
        pytest.skip(message)
    with path.open("rb") as source:
        patterns = {
            label: re.compile(pattern, re.IGNORECASE)
            for label, pattern in tomllib.load(source)["patterns"].items()
        }
    texts, findings = _tracked_texts()
    findings += [
        _finding(name, text, match, label)
        for name, text in texts
        for label, pattern in patterns.items()
        for match in pattern.finditer(text)
    ]
    assert not findings, "\n" + "\n".join(findings)
