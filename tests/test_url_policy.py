import ipaddress

import pytest

from app.url_policy import (
    get_public_allowlist,
    PublicAllowlist,
    screen_adlist_url,
    URLPolicyError,
    normalize_instance_url,
    validate_instance_destination,
)


EMPTY = PublicAllowlist(frozenset(), frozenset(), ())


def resolver_for(*addresses):
    def resolve(_host, _port, *, type):
        return [(2, type, 6, "", (address, 0)) for address in addresses]

    return resolve


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("HTTP://PiHole.LAN/", "http://pihole.lan"),
        ("https://pihole.lan:8443", "https://pihole.lan:8443"),
        ("http://[fd00::53]/", "http://[fd00::53]"),
    ],
)
def test_normalizes_origin_only_urls(raw, normalized):
    assert normalize_instance_url(raw) == normalized


@pytest.mark.parametrize(
    "url",
    [
        "ftp://pihole.lan",
        "http://user:password@pihole.lan",
        "http://pihole.lan/admin",
        "http://pihole.lan?query=yes",
        "http://pihole.lan#fragment",
        "http://pihole.lan:99999",
        "http://pihole.lan\\@public.example",
        " http://pihole.lan",
        "http://pihole.lan\n",
    ],
)
def test_rejects_unsafe_or_ambiguous_urls(url):
    with pytest.raises(URLPolicyError):
        normalize_instance_url(url)


@pytest.mark.parametrize("address", ["10.1.2.3", "172.16.1.2", "192.168.10.2", "fd12::53"])
def test_private_lan_destinations_are_allowed(address):
    rendered = f"[{address}]" if ":" in address else address
    assert validate_instance_destination(f"https://{rendered}", allowlist=EMPTY) == f"https://{rendered}"


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "::1", "169.254.169.254", "100.100.100.200", "fd00:ec2::254", "fe80::1", "0.0.0.0", "224.0.0.1"],
)
def test_special_destinations_are_always_denied(address):
    rendered = f"[{address}]" if ":" in address else address
    allow_everything = PublicAllowlist(
        frozenset(),
        frozenset(),
        (ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0")),
    )
    with pytest.raises(URLPolicyError, match="prohibited"):
        validate_instance_destination(f"http://{rendered}", allowlist=allow_everything)


def test_public_address_requires_allowlist():
    with pytest.raises(URLPolicyError, match="not allowlisted"):
        validate_instance_destination("https://8.8.8.8", allowlist=EMPTY)


@pytest.mark.parametrize(
    "allowlist",
    [
        PublicAllowlist(frozenset(), frozenset({ipaddress.ip_address("8.8.8.8")}), ()),
        PublicAllowlist(frozenset(), frozenset(), (ipaddress.ip_network("8.8.8.0/24"),)),
    ],
)
def test_exact_ip_and_cidr_allowlist_entries_allow_public_addresses(allowlist):
    assert validate_instance_destination("https://8.8.8.8", allowlist=allowlist) == "https://8.8.8.8"


def test_exact_hostname_allowlist_allows_public_dns_answers():
    allowlist = PublicAllowlist(frozenset({"pihole.example.com"}), frozenset(), ())
    assert validate_instance_destination(
        "https://PIHOLE.EXAMPLE.COM/",
        allowlist=allowlist,
        resolver=resolver_for("8.8.8.8"),
    ) == "https://pihole.example.com"


def test_non_allowlisted_hostname_must_resolve_only_to_permitted_private_addresses():
    assert validate_instance_destination(
        "http://pihole.lan", allowlist=EMPTY, resolver=resolver_for("192.168.1.2", "fd00::2")
    ) == "http://pihole.lan"
    with pytest.raises(URLPolicyError, match="not allowlisted"):
        validate_instance_destination(
            "http://pihole.lan", allowlist=EMPTY, resolver=resolver_for("192.168.1.2", "8.8.8.8")
        )


def test_environment_allowlist_supports_hostname_ip_and_cidr(monkeypatch):
    monkeypatch.setenv("FILTERHELM_PUBLIC_ALLOWLIST", "pihole.example.com,8.8.8.8,1.1.1.0/24")
    allowlist = PublicAllowlist.from_environment()
    assert allowlist.hostnames == {"pihole.example.com"}
    assert ipaddress.ip_address("8.8.8.8") in allowlist.addresses
    assert ipaddress.ip_address("1.1.1.1") in allowlist.networks[0]


def test_environment_allowlist_is_cached(monkeypatch):
    monkeypatch.setenv("FILTERHELM_PUBLIC_ALLOWLIST", "8.8.8.8")
    first = get_public_allowlist()
    monkeypatch.setenv("FILTERHELM_PUBLIC_ALLOWLIST", "1.1.1.1")

    assert get_public_allowlist() is first
    get_public_allowlist.cache_clear()
    assert ipaddress.ip_address("1.1.1.1") in get_public_allowlist().addresses


def test_adlist_url_allows_a_public_list_without_an_allowlist(monkeypatch):
    monkeypatch.delenv("FILTERHELM_PUBLIC_ALLOWLIST", raising=False)
    url = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/tif.txt"
    assert screen_adlist_url(url) == url


def test_adlist_url_is_canonicalized():
    assert screen_adlist_url("https://Example.COM/list.txt?format=hosts") == "https://example.com/list.txt?format=hosts"
    assert screen_adlist_url("https://Example.com/") == "https://example.com"


def test_adlist_url_never_resolves(monkeypatch):
    def fail_resolver(*_args, **_kwargs):
        raise AssertionError("resolver must not be called")

    monkeypatch.setattr("socket.getaddrinfo", fail_resolver)
    for url in ("https://example.com/list", "http://lists.lan/hosts"):
        assert screen_adlist_url(url) == url


def test_adlist_url_allows_rfc1918_and_hostnames():
    for url in ("http://lists.lan/hosts", "http://192.168.1.4/hosts"):
        assert screen_adlist_url(url) == url


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest", "http://127.0.0.1/list", "http://[::1]/list"])
def test_adlist_url_rejects_prohibited_literal_addresses(url):
    with pytest.raises(URLPolicyError, match="prohibited"):
        screen_adlist_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.com/list", "ftp://example.com/list",
        "https://example.com/line\nfeed", "https://example.com/list#frag",
        "https://example.com:99999/list", " https://example.com/list",
    ],
)
def test_adlist_url_rejects_unsafe_syntax(url):
    with pytest.raises(URLPolicyError):
        screen_adlist_url(url)


def test_instance_url_policy_is_unchanged():
    with pytest.raises(URLPolicyError, match="not allowlisted"):
        validate_instance_destination("https://example.com", allowlist=EMPTY, resolver=resolver_for("8.8.8.8"))


def test_update_check_destination_accepts_public_answers():
    from app.url_policy import resolve_update_check_destination
    destination = resolve_update_check_destination(resolver=resolver_for('8.8.8.8', '2606:4700:4700::1111'))
    assert destination.url == 'https://api.github.com'
    assert destination.host == 'api.github.com'
    assert destination.port is None
    assert set(destination.addresses) == {ipaddress.ip_address('8.8.8.8'), ipaddress.ip_address('2606:4700:4700::1111')}


@pytest.mark.parametrize('address', ['127.0.0.1', '169.254.169.254', '10.0.0.5', '192.168.1.2', 'fd00::1', '::ffff:10.0.0.1'])
@pytest.mark.parametrize('mixed', [False, True])
def test_update_check_destination_rejects_non_public_answers(address, mixed):
    from app.url_policy import resolve_update_check_destination
    addresses = [address, '8.8.8.8'] if mixed else [address]
    with pytest.raises(URLPolicyError, match='non-public address'):
        resolve_update_check_destination(resolver=resolver_for(*addresses))


def test_update_check_destination_ignores_public_allowlist(monkeypatch):
    from app.url_policy import resolve_update_check_destination
    monkeypatch.setenv('FILTERHELM_PUBLIC_ALLOWLIST', '10.0.0.0/8')
    with pytest.raises(URLPolicyError):
        resolve_update_check_destination(resolver=resolver_for('10.0.0.5'))


def test_update_check_destination_resolution_failure():
    from app.url_policy import resolve_update_check_destination
    def fail(*args, **kwargs):
        raise OSError('DNS unavailable')
    with pytest.raises(URLPolicyError, match='Update check host could not be resolved'):
        resolve_update_check_destination(resolver=fail)
    with pytest.raises(URLPolicyError, match='did not resolve to an address'):
        resolve_update_check_destination(resolver=resolver_for())
