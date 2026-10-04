import io
import zipfile

from app import pihole_client
from app.pihole_client import Adlist, Client, Domain, Group, Session
from app.url_policy import PublicAllowlist, URLPolicyError, resolve_instance_destination
import json
import httpx
import pytest
import ssl


EMPTY = PublicAllowlist(frozenset(), frozenset(), ())


def private_destination(value):
    return resolve_instance_destination(
        value,
        allowlist=EMPTY,
        resolver=lambda _host, port, *, type: [(2, type, 6, "", ("192.168.1.2", port or 0))],
    )


def test_client_explicitly_disables_redirects(monkeypatch):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    client = pihole_client.PiholeClient("https://pihole.lan", "secret")
    try:
        assert client._client.follow_redirects is False
    finally:
        client._client.close()


def test_client_configures_tls_verification_on_pinned_transport(monkeypatch):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    verified = pihole_client.PiholeClient("https://pihole.lan", "secret")
    unverified = pihole_client.PiholeClient(
        "https://pihole.lan", "secret", verify_ssl=False
    )
    try:
        verified_context = verified._client._transport._pool._ssl_context
        unverified_context = unverified._client._transport._pool._ssl_context
        assert verified_context.verify_mode == ssl.CERT_REQUIRED
        assert verified_context.check_hostname is True
        assert unverified_context.verify_mode == ssl.CERT_NONE
    finally:
        verified._client.close()
        unverified._client.close()


def test_upstream_error_does_not_include_response_body(monkeypatch):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    client = pihole_client.PiholeClient("https://pihole.lan", "secret")
    request = httpx.Request("POST", "https://pihole.lan/api/auth")
    client._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(500, text="sensitive upstream details", request=request)
        ),
        base_url="https://pihole.lan",
    )
    try:
        with pytest.raises(pihole_client.PiholeAPIError) as caught:
            client._authenticate()
        assert "sensitive upstream details" not in str(caught.value)
        assert "HTTP 500" in str(caught.value)
    finally:
        client._client.close()


@pytest.mark.parametrize(
    ("body", "enabled", "timer"),
    [
        ({"blocking": True, "timer": None}, True, None),
        ({"blocking": "disabled", "timer": 9.5}, False, 9.5),
    ],
)
def test_blocking_state_accepts_current_and_legacy_response_shapes(body, enabled, timer):
    response = httpx.Response(200, json=body)
    state = pihole_client.PiholeClient._blocking_state(response)
    assert state.enabled is enabled
    assert state.timer == timer


def test_timed_pause_sends_numeric_timer(monkeypatch):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"blocking": False, "timer": 300}, request=request)

    client = pihole_client.PiholeClient("https://pihole.lan", "secret")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond), base_url="https://pihole.lan")
    client._session = Session(sid="session", csrf="csrf")
    try:
        state = client.set_blocking(False, 300)
        assert requests[0].url.path == "/api/dns/blocking"
        assert requests[0].method == "POST"
        assert requests[0].content == b'{"blocking":false,"timer":300}'
        assert state.timer == 300
    finally:
        client._client.close()


def test_adlist_requests_include_block_type_and_preserve_groups(monkeypatch):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"lists": [{
                    "address": "https://lists.example/path/block.txt",
                    "comment": "Test",
                    "enabled": True,
                    "groups": [0, 2],
                    "date_updated": 1_700_000_000,
                    "number": 100,
                    "invalid_domains": 1,
                }]},
                request=request,
            )
        return httpx.Response(204 if request.method == "DELETE" else 200, request=request)

    client = pihole_client.PiholeClient("https://pihole.lan", "secret")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond), base_url="https://pihole.lan")
    client._session = Session(sid="session", csrf="csrf")
    adlist = Adlist("https://lists.example/path/block.txt", "Test", True, (0, 2), None, None, None)
    try:
        assert client.get_adlists()[0].groups == (0, 2)
        client.add_adlist("https://lists.example/new.txt", "New")
        client.update_adlist(adlist, enabled=False, groups=[0, 5])
        client.delete_adlist(adlist.address)
        client.update_gravity()
    finally:
        client._client.close()

    assert all(request.url.params.get("type") == "block" for request in requests[:4])
    assert json.loads(requests[1].content) == {
        "address": "https://lists.example/new.txt", "comment": "New", "enabled": True, "groups": [0]
    }
    assert b"https%3A%2F%2Flists.example%2Fpath%2Fblock.txt" in requests[2].url.raw_path
    assert json.loads(requests[2].content) == {"comment": "Test", "enabled": False, "groups": [0, 5]}
    assert requests[4].url.path == "/api/action/gravity"


def test_domain_requests_use_type_kind_path_segments(monkeypatch):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"domains": [{
                    "id": 1,
                    "domain": "ads.example.com",
                    "type": "deny",
                    "kind": "exact",
                    "comment": "Test",
                    "enabled": True,
                    "groups": [0, 2],
                    "date_added": 1_700_000_000,
                    "date_modified": 1_700_000_001,
                }]},
                request=request,
            )
        return httpx.Response(204 if request.method == "DELETE" else 200, request=request)

    client = pihole_client.PiholeClient("https://pihole.lan", "secret")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond), base_url="https://pihole.lan")
    client._session = Session(sid="session", csrf="csrf")
    item = Domain(1, "ads.example.com", "deny", "exact", "Test", True, (0, 2), None, None)
    try:
        assert client.get_domains()[0].groups == (0, 2)
        client.add_domain("ads.example.com", "deny", "exact", "New")
        client.update_domain(item, enabled=False, groups=[0, 5])
        client.delete_domain(item.domain, item.type, item.kind)
    finally:
        client._client.close()

    assert requests[0].url.path == "/api/domains"
    assert not requests[0].url.params
    assert requests[1].url.path == "/api/domains/deny/exact"
    assert json.loads(requests[1].content) == {
        "domain": "ads.example.com", "comment": "New", "enabled": True, "groups": [0]
    }
    assert requests[2].url.path == "/api/domains/deny/exact/ads.example.com"
    assert json.loads(requests[2].content) == {"comment": "Test", "enabled": False, "groups": [0, 5]}
    assert requests[3].url.path == "/api/domains/deny/exact/ads.example.com"


def test_group_requests_use_name_path_segment(monkeypatch):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    requests = []
    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"groups": [{"id": 5, "name": "Kids", "comment": "Test", "enabled": True}]}, request=request)
        return httpx.Response(204 if request.method == "DELETE" else 200, request=request)
    client = pihole_client.PiholeClient("https://pihole.lan", "secret")
    client._client.close(); client._client = httpx.Client(transport=httpx.MockTransport(respond), base_url="https://pihole.lan"); client._session = Session("session", "csrf")
    try:
        assert client.get_groups()[0].name == "Kids"
        client.add_group("Kids")
        client.update_group(Group(5, "Kids", "Test", True, None, None), enabled=False)
        client.delete_group("Kids")
    finally: client._client.close()
    assert [request.url.path for request in requests] == ["/api/groups", "/api/groups", "/api/groups/Kids", "/api/groups/Kids"]
    assert json.loads(requests[1].content) == {"name": "Kids", "comment": "", "enabled": True}
    assert json.loads(requests[2].content) == {"name": "Kids", "comment": "Test", "enabled": False}


def test_client_requests_use_client_path_segment(monkeypatch):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    requests = []
    def respond(request):
        requests.append(request)
        if request.method == "GET": return httpx.Response(200, json={"clients": [{"id": 1, "client": "192.168.1.50", "comment": "Test", "groups": [0]}]}, request=request)
        return httpx.Response(204 if request.method == "DELETE" else 200, request=request)
    client = pihole_client.PiholeClient("https://pihole.lan", "secret")
    client._client.close(); client._client = httpx.Client(transport=httpx.MockTransport(respond), base_url="https://pihole.lan"); client._session = Session("session", "csrf")
    try:
        assert client.get_clients()[0].client == "192.168.1.50"
        client.add_client("192.168.1.50")
        client.update_client(Client(1, "192.168.1.50", "Test", (0,), None, None), groups=[0, 5])
        client.delete_client("192.168.1.50")
        client.delete_client("192.168.2.0/24")
    finally: client._client.close()
    assert requests[0].url.path == "/api/clients"
    assert json.loads(requests[1].content) == {"client": "192.168.1.50", "comment": "", "groups": [0]}
    assert json.loads(requests[2].content) == {"comment": "Test", "groups": [0, 5]}
    assert b"192.168.2.0%2F24" in requests[4].url.raw_path


def test_client_pins_validated_address_and_preserves_hostname(monkeypatch):
    destination = private_destination("https://pihole.lan:8443")
    captured = []

    def handle_request(_transport, request):
        captured.append(request)
        return httpx.Response(200, request=request)

    monkeypatch.setattr(pihole_client, "resolve_instance_destination", lambda _value: destination)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    client = pihole_client.PiholeClient("https://pihole.lan:8443", "secret")
    try:
        client._client.get("/api/test")
    finally:
        client._client.close()

    assert captured[0].url.host == "192.168.1.2"
    assert captured[0].url.port == 8443
    assert captured[0].headers["Host"] == "pihole.lan:8443"
    assert captured[0].extensions["sni_hostname"] == "pihole.lan"


@pytest.mark.parametrize("address", ["169.254.169.254", "8.8.8.8"])
def test_client_rejects_unapproved_destination_before_http(monkeypatch, address):
    calls = []

    def resolve(value):
        return resolve_instance_destination(
            value,
            allowlist=EMPTY,
            resolver=lambda _host, port, *, type: [(2, type, 6, "", (address, port or 0))],
        )

    monkeypatch.setattr(pihole_client, "resolve_instance_destination", resolve)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda *_args: calls.append(True))

    with pytest.raises(URLPolicyError):
        pihole_client.PiholeClient("http://pihole.lan", "secret")

    assert calls == []


def test_pihole_api_error_status_code_defaults_to_none():
    error = pihole_client.PiholeAPIError("message")
    assert str(error) == "message"
    assert error.status_code is None


def search_body():
    return {"search": {
        "domains": [{"id": 1, "domain": "foo/bar.example", "type": "allow", "kind": "exact",
                     "comment": "Override", "enabled": True, "groups": [0, 2],
                     "date_added": 10, "date_modified": 11}],
        "gravity": [{"id": 2, "domain": "foo/bar.example", "address": "https://lists.example/hosts",
                     "type": "block", "comment": "Hosts", "enabled": True, "groups": [0]}],
        "results": {"domains": {"exact": 1, "regex": 0}, "gravity": {"allow": 0, "block": 1}, "total": 2},
        "parameters": {"partial": True, "N": 7},
    }, "took": 0.001}


def search_client(monkeypatch, respond):
    monkeypatch.setattr(pihole_client, "resolve_instance_destination", private_destination)
    client = pihole_client.PiholeClient("https://pihole.lan", "secret")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond), base_url="https://pihole.lan")
    client._session = Session("session", "csrf")
    return client


def test_search_domain_quotes_term_and_parses_matches(monkeypatch):
    def respond(request):
        assert request.method == "GET"
        assert request.url.raw_path.split(b"?")[0] == b"/api/search/foo%2Fbar.example"
        assert dict(request.url.params) == {"partial": "true", "N": "7"}
        return httpx.Response(200, json=search_body())

    client = search_client(monkeypatch, respond)
    try:
        result = client.search_domain("foo/bar.example", partial=True, limit=7)
        assert result.domains == (Domain(1, "foo/bar.example", "allow", "exact", "Override", True, (0, 2), 10, 11),)
        assert result.gravity == (pihole_client.GravityMatch(2, "foo/bar.example", "https://lists.example/hosts", "block", "Hosts", True, (0,)),)
        assert result.counts == pihole_client.SearchCounts(1, 0, 0, 1, 2)
    finally:
        client._client.close()


@pytest.mark.parametrize("case", ["missing_search", "missing_address", "bad_domains", "bad_gravity", "bad_results", "bool_group", "http_error"])
def test_search_domain_rejects_invalid_response(monkeypatch, case):
    body = search_body()
    status = 200
    if case == "missing_search":
        body = {}
    elif case == "missing_address":
        del body["search"]["gravity"][0]["address"]
    elif case == "bool_group":
        body["search"]["gravity"][0]["groups"] = [True]
    elif case.startswith("bad_"):
        body["search"][case.removeprefix("bad_")] = None
    else:
        status = 503
    client = search_client(monkeypatch, lambda _: httpx.Response(status, json=body))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if status == 503 else "invalid"):
            client.search_domain("example.com")
    finally:
        client._client.close()


@pytest.mark.parametrize("field", ["total", "domains.exact", "domains.regex", "gravity.allow", "gravity.block"])
@pytest.mark.parametrize("value", [None, "1", True])
def test_search_domain_requires_complete_result_counts(monkeypatch, field, value):
    body = search_body()
    container = body["search"]["results"]
    parts = field.split(".")
    if len(parts) == 2:
        container = container[parts[0]]
    if value is None:
        del container[parts[-1]]
    else:
        container[parts[-1]] = value
    client = search_client(monkeypatch, lambda _: httpx.Response(200, json=body))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="invalid result counts"):
            client.search_domain("example.com")
    finally:
        client._client.close()


# Raw API shapes reconstructed from plan 0014's confirmed Contracts example.
def stats_summary_body():
    return {"queries": {"total": 156, "blocked": 0, "percent_blocked": 0,
                        "unique_domains": 42, "forwarded": 100, "cached": 56},
            "clients": {"active": 2, "total": 2},
            "gravity": {"domains_being_blocked": 79541, "last_update": 1788845689}}


def top_list_body(kind):
    rows = ([{"domain": "1.in-addr.arpa", "count": 25}]
            if kind == "domains" else [{"ip": "::", "name": "pi.hole", "count": 97},
                                       {"ip": "127.0.0.1", "name": None, "count": 59}])
    return {kind: rows, "total_queries": 156, "blocked_queries": 0}


def upstreams_body():
    return {"upstreams": [
        {"ip": ip, "name": name, "port": port, "count": count,
         "statistics": {"response": response, "variance": variance}}
        for ip, name, port, count, response, variance in [
            ("blocklist", "blocklist", -1, 0, 0, 0), ("cache", "cache", -1, 56, 0, 0),
            ("1.0.0.2", "security.cloudflare-dns.com", 53, 62, 0.020752204044927818, 0.0007558185700903987),
            ("1.1.1.2", "security.cloudflare-dns.com", 53, 38, 0.01903403675095718, 0.0006069763137027048),
        ]], "total_queries": 156, "forwarded_queries": 100}


def queries_body():
    return {"queries": [
        {"id": id, "time": timestamp, "type": "PTR", "domain": domain,
         "cname": None, "status": "CACHE_STALE", "client": {"ip": "127.0.0.1", "name": "localhost.lan"},
         "upstream": None, "reply": {"type": "DOMAIN", "time": reply_time}}
        for id, timestamp, domain, reply_time in [
            (37598335, 1789268400.9673905, "2.1.1.1.in-addr.arpa", 1.0967254638671875e-05),
            (37598334, 1789268400.966724, "2.0.0.1.in-addr.arpa", 8.34465026855469e-06),
        ]], "recordsTotal": 156, "recordsFiltered": 32, "earliest_timestamp": 1789184400}


def test_get_stats_summary_parses_confirmed_shape(monkeypatch):
    def respond(request):
        assert request.method == "GET"
        assert request.url.path == "/api/stats/summary"
        assert not request.url.params
        return httpx.Response(200, json=stats_summary_body())
    client = search_client(monkeypatch, respond)
    try:
        assert client.get_stats_summary() == pihole_client.StatsSummary(156, 0, 0, 42, 100, 56, 2, 2, 79541, 1788845689)
    finally:
        client._client.close()


@pytest.mark.parametrize("case", ["missing_total", "bool_blocked", "missing_gravity", "http_error"])
def test_get_stats_summary_rejects_invalid_response(monkeypatch, case):
    body = stats_summary_body()
    if case == "missing_total":
        del body["queries"]["total"]
    if case == "bool_blocked":
        body["queries"]["blocked"] = True
    if case == "missing_gravity":
        del body["gravity"]
    client = search_client(monkeypatch, lambda _: httpx.Response(503 if case == "http_error" else 200, json=body))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if case == "http_error" else "invalid response"):
            client.get_stats_summary()
    finally:
        client._client.close()


def test_top_lists_send_blocked_flag_and_count(monkeypatch):
    def respond(request):
        kind = request.url.path.removeprefix("/api/stats/top_")
        assert request.method == "GET"
        assert kind in ("domains", "clients")
        assert dict(request.url.params) == {"blocked": "true" if kind == "domains" else "false", "count": "10"}
        return httpx.Response(200, json=top_list_body(kind))
    client = search_client(monkeypatch, respond)
    try:
        domains = client.get_top_domains(blocked=True, count=10)
        assert domains == pihole_client.TopDomains((pihole_client.TopDomain("1.in-addr.arpa", 25),), 156, 0)
        clients = client.get_top_clients(blocked=False, count=10)
        assert clients == pihole_client.TopClients((pihole_client.TopClient("::", "pi.hole", 97), pihole_client.TopClient("127.0.0.1", None, 59)), 156, 0)
    finally:
        client._client.close()


@pytest.mark.parametrize("kind", ["domains", "clients"])
@pytest.mark.parametrize("case", ["missing_list", "missing_count", "bool_count", "missing_total", "http_error"])
def test_top_lists_reject_invalid_response(monkeypatch, kind, case):
    body = top_list_body(kind)
    if case == "missing_list":
        del body[kind]
    if case == "missing_count":
        del body[kind][0]["count"]
    if case == "bool_count":
        body[kind][0]["count"] = True
    if case == "missing_total":
        del body["total_queries"]
    client = search_client(monkeypatch, lambda _: httpx.Response(503 if case == "http_error" else 200, json=body))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if case == "http_error" else "invalid response"):
            getattr(client, f"get_top_{kind}")(blocked=False, count=10)
    finally:
        client._client.close()


def test_get_upstreams_keeps_pseudo_entries_and_statistics(monkeypatch):
    body = upstreams_body()
    def respond(request):
        assert request.method == "GET"
        assert request.url.path == "/api/stats/upstreams"
        assert not request.url.params
        return httpx.Response(200, json=body)
    client = search_client(monkeypatch, respond)
    try:
        result = client.get_upstreams()
        assert len(result.upstreams) == 4
        assert result.upstreams[1] == pihole_client.Upstream("cache", "cache", -1, 56, 0, 0)
        assert result.upstreams[2] == pihole_client.Upstream("1.0.0.2", "security.cloudflare-dns.com", 53, 62, 0.020752204044927818, 0.0007558185700903987)
        assert result.total_queries == 156
        assert result.forwarded_queries == 100
        body["upstreams"][0].update(ip=None, name=None)
        assert client.get_upstreams().upstreams[0].ip is None
        assert client.get_upstreams().upstreams[0].name is None
    finally:
        client._client.close()


@pytest.mark.parametrize("case", ["missing_list", "missing_statistics", "string_response", "bool_port", "http_error"])
def test_get_upstreams_rejects_invalid_response(monkeypatch, case):
    body = upstreams_body()
    if case == "missing_list":
        del body["upstreams"]
    if case == "missing_statistics":
        del body["upstreams"][0]["statistics"]
    if case == "string_response":
        body["upstreams"][0]["statistics"]["response"] = "0"
    if case == "bool_port":
        body["upstreams"][0]["port"] = True
    client = search_client(monkeypatch, lambda _: httpx.Response(503 if case == "http_error" else 200, json=body))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if case == "http_error" else "invalid response"):
            client.get_upstreams()
    finally:
        client._client.close()


def test_get_queries_sends_only_provided_filters(monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/queries"
        return httpx.Response(200, json=queries_body())
    client = search_client(monkeypatch, respond)
    try:
        client.get_queries(start=1700000000.7, length=50, domain="*.example", status="GRAVITY")
        client.get_queries(length=20)
        client.get_queries(client_ip="::", client_name="nas")
    finally:
        client._client.close()
    assert dict(requests[0].url.params) == {"length": "50", "from": "1700000000", "domain": "*.example", "status": "GRAVITY"}
    assert dict(requests[1].url.params) == {"length": "20"}
    assert dict(requests[2].url.params) == {"length": "100", "client_ip": "::", "client_name": "nas"}


def test_get_queries_parses_records_and_metadata(monkeypatch):
    body = queries_body()
    client = search_client(monkeypatch, lambda _: httpx.Response(200, json=body))
    try:
        page = client.get_queries()
        assert page.records_total == 156
        assert page.records_filtered == 32
        assert page.earliest_timestamp == 1789184400
        assert len(page.queries) == 2
        assert page.queries[0] == pihole_client.QueryRecord(37598335, 1789268400.9673905, "PTR", "2.1.1.1.in-addr.arpa", None, "CACHE_STALE", "127.0.0.1", "localhost.lan", None, "DOMAIN", 1.0967254638671875e-05)
        body["queries"][0]["client"]["name"] = None
        body["queries"][0]["reply"] = {}
        row = client.get_queries().queries[0]
        assert row.client_name is None
        assert row.reply_type is None
        assert row.reply_time is None
    finally:
        client._client.close()


@pytest.mark.parametrize("case", ["missing_list", "missing_filtered", "missing_id", "missing_ip", "http_error"])
def test_get_queries_rejects_invalid_response(monkeypatch, case):
    body = queries_body()
    if case == "missing_list":
        del body["queries"]
    if case == "missing_filtered":
        del body["recordsFiltered"]
    if case == "missing_id":
        del body["queries"][0]["id"]
    if case == "missing_ip":
        del body["queries"][0]["client"]["ip"]
    client = search_client(monkeypatch, lambda _: httpx.Response(503 if case == "http_error" else 200, json=body))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if case == "http_error" else "invalid"):
            client.get_queries()
    finally:
        client._client.close()


# Raw health shapes confirmed by plan 0015 against lab-pihole.
def system_info_body():
    return {"system": {"uptime": 1296249, "memory": {
        "ram": {"total": 2097152, "used": 54137, "available": 2042507, "%used": 2.58},
        "swap": {"total": 8388604, "used": 3017108, "%used": 35.97}},
        "cpu": {"nprocs": 4, "%cpu": 6.97, "load": {"raw": [0.61, 1.33, 1.23, 99]}}}}


def health_response(endpoint, body, status=200):
    def respond(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/info/{endpoint}"
        assert not request.url.params
        assert request.headers["X-FTL-SID"] == "session"
        assert request.headers["X-FTL-CSRF"] == "csrf"
        return httpx.Response(status, json=body)
    return respond


def test_get_system_info_parses_live_shape(monkeypatch):
    client = search_client(monkeypatch, health_response("system", system_info_body()))
    try:
        assert client.get_system_info() == pihole_client.SystemInfo(
            1296249, 2097152, 54137, 2042507, 2.58, 8388604, 3017108, 35.97,
            4, 6.97, (0.61, 1.33, 1.23))
    finally:
        client._client.close()


@pytest.mark.parametrize("case", ["http_error", "missing_system", "missing_memory", "bool_cores", "string_uptime", "short_load"])
def test_get_system_info_rejects_invalid_response(monkeypatch, case):
    body = system_info_body()
    if case == "missing_system":
        del body["system"]
    elif case == "missing_memory":
        del body["system"]["memory"]
    elif case == "bool_cores":
        body["system"]["cpu"]["nprocs"] = True
    elif case == "string_uptime":
        body["system"]["uptime"] = "1296249"
    elif case == "short_load":
        body["system"]["cpu"]["load"]["raw"] = [0.61, 1.33]
    client = search_client(monkeypatch, health_response("system", body, 503 if case == "http_error" else 200))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if case == "http_error" else "invalid response"):
            client.get_system_info()
    finally:
        client._client.close()


def test_get_ftl_info_keeps_milliseconds(monkeypatch):
    body = {"ftl": {"uptime": 432620297.203082, "%cpu": 0.025, "%mem": 1.43}}
    client = search_client(monkeypatch, health_response("ftl", body))
    try:
        assert client.get_ftl_info() == pihole_client.FtlInfo(432620297.203082, 0.025, 1.43)
    finally:
        client._client.close()


@pytest.mark.parametrize("case", ["http_error", "missing_ftl", "missing_uptime", "string_cpu"])
def test_get_ftl_info_rejects_invalid_response(monkeypatch, case):
    body = {"ftl": {"uptime": 432620297.203082, "%cpu": 0.025, "%mem": 1.43}}
    if case == "missing_ftl":
        del body["ftl"]
    elif case == "missing_uptime":
        del body["ftl"]["uptime"]
    elif case == "string_cpu":
        body["ftl"]["%cpu"] = "0.025"
    client = search_client(monkeypatch, health_response("ftl", body, 503 if case == "http_error" else 200))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if case == "http_error" else "invalid response"):
            client.get_ftl_info()
    finally:
        client._client.close()


def test_get_database_info_parses_top_level_body(monkeypatch):
    body = {"size": 460140544, "queries_disk": 2636298, "queries": 163, "sqlite_version": "3.53.1"}
    client = search_client(monkeypatch, health_response("database", body))
    try:
        assert client.get_database_info() == pihole_client.DatabaseInfo(460140544, 2636298, "3.53.1")
    finally:
        client._client.close()


@pytest.mark.parametrize("case", ["http_error", "bool_size", "missing_queries_disk", "int_version"])
def test_get_database_info_rejects_invalid_response(monkeypatch, case):
    body = {"size": 460140544, "queries_disk": 2636298, "sqlite_version": "3.53.1"}
    if case == "bool_size":
        body["size"] = True
    elif case == "missing_queries_disk":
        del body["queries_disk"]
    elif case == "int_version":
        body["sqlite_version"] = 3
    client = search_client(monkeypatch, health_response("database", body, 503 if case == "http_error" else 200))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if case == "http_error" else "invalid response"):
            client.get_database_info()
    finally:
        client._client.close()


def test_get_sensors_parses_temperature(monkeypatch):
    client = search_client(monkeypatch, health_response("sensors", {"sensors": {"cpu_temp": 57, "hot_limit": 60, "unit": "C"}}))
    try:
        assert client.get_sensors() == pihole_client.SensorsInfo(57.0, 60.0, "C")
    finally:
        client._client.close()


@pytest.mark.parametrize("temperature", ["absent", None, -1])
def test_get_sensors_without_temperature_returns_none(monkeypatch, temperature):
    body = {"sensors": {} if temperature == "absent" else {"cpu_temp": temperature}}
    client = search_client(monkeypatch, health_response("sensors", body))
    try:
        assert client.get_sensors() == pihole_client.SensorsInfo(None, None, "C")
    finally:
        client._client.close()


@pytest.mark.parametrize("status,body", [(503, {}), (200, {"sensors": []})])
def test_get_sensors_rejects_http_error_and_bad_body(monkeypatch, status, body):
    client = search_client(monkeypatch, health_response("sensors", body, status))
    try:
        with pytest.raises(pihole_client.PiholeAPIError, match="HTTP 503" if status == 503 else "invalid response"):
            client.get_sensors()
    finally:
        client._client.close()


def teleporter_zip(
    members: dict[str, bytes] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    if members is None:
        members = {
            "etc/pihole/pihole.toml": b'[dns]\nupstreams = ["1.1.1.1"]\n',
            "etc/pihole/gravity.db": b"sqlite",
        }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_get_teleporter_returns_zip_bytes(monkeypatch):
    data = teleporter_zip()

    def respond(request):
        assert request.method == "GET"
        assert request.url.path == "/api/teleporter"
        assert request.headers["X-FTL-SID"] == "session"
        return httpx.Response(200, content=data)

    client = search_client(monkeypatch, respond)
    try:
        assert client.get_teleporter() == data
    finally:
        client._client.close()


def test_get_teleporter_rejects_non_200(monkeypatch):
    client = search_client(monkeypatch, lambda _: httpx.Response(401))
    try:
        with pytest.raises(pihole_client.PiholeAPIError) as caught:
            client.get_teleporter()
        assert str(caught.value) == "GET /api/teleporter failed with HTTP 401"
        assert caught.value.status_code == 401
    finally:
        client._client.close()


def test_get_teleporter_rejects_non_zip(monkeypatch):
    client = search_client(monkeypatch, lambda _: httpx.Response(200, content=b"<html>Error</html>"))
    try:
        with pytest.raises(pihole_client.PiholeAPIError) as caught:
            client.get_teleporter()
        assert str(caught.value) == "Teleporter response was not a zip archive"
    finally:
        client._client.close()


def test_get_teleporter_enforces_size_cap(monkeypatch):
    monkeypatch.setattr(pihole_client, "TELEPORTER_MAX_BYTES", 16)
    client = search_client(monkeypatch, lambda _: httpx.Response(200, content=b"x" * 32))
    try:
        with pytest.raises(pihole_client.PiholeAPIError) as caught:
            client.get_teleporter()
        assert str(caught.value) == "Teleporter archive exceeds the size limit"
    finally:
        client._client.close()


def test_get_teleporter_rejects_truncated_zip():
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(teleporter_zip()[:-10])
    assert str(caught.value) == "Teleporter response was not a zip archive"


@pytest.mark.parametrize("error", [
    UnicodeDecodeError("utf-8", b"", 0, 1, "bad"),
    NotImplementedError(),
])
def test_check_teleporter_archive_maps_open_errors(monkeypatch, error):
    data = teleporter_zip()

    def fail_open(*args, **kwargs):
        raise error

    monkeypatch.setattr(pihole_client.zipfile, "ZipFile", fail_open)
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(data)
    assert str(caught.value) == "Teleporter response was not a zip archive"


def test_get_teleporter_rejects_corrupt_member(monkeypatch):
    data = bytearray(teleporter_zip(compression=zipfile.ZIP_STORED))
    offset = data.find(b'[dns]\nupstreams = ["1.1.1.1"]\n')
    assert offset >= 0
    data[offset] ^= 1
    client = search_client(monkeypatch, lambda _: httpx.Response(200, content=bytes(data)))
    try:
        with pytest.raises(pihole_client.PiholeAPIError) as caught:
            client.get_teleporter()
        assert str(caught.value) == "Teleporter archive is corrupt"
    finally:
        client._client.close()


def test_check_teleporter_archive_rejects_duplicate_names():
    marker = b"# first copy marker\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("etc/pihole/pihole.toml", marker)
        archive.writestr("etc/pihole/gravity.db", b"sqlite")
        with pytest.warns(UserWarning):
            archive.writestr("etc/pihole/pihole.toml", b'[dns]\nupstreams = ["1.1.1.1"]\n')
    data = bytearray(buffer.getvalue())
    offset = data.find(marker)
    assert offset >= 0
    data[offset] ^= 1
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(bytes(data))
    assert str(caught.value) == "Teleporter archive has duplicate entries"


@pytest.mark.parametrize("compression", [zipfile.ZIP_LZMA, zipfile.ZIP_BZIP2])
def test_check_teleporter_archive_rejects_unsupported_compression(compression):
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(teleporter_zip(compression=compression))
    assert str(caught.value) == "Teleporter archive uses unsupported compression"


def test_check_teleporter_archive_rejects_encrypted_member():
    data = bytearray(teleporter_zip())
    data[data.find(b"PK\x01\x02") + 8] |= 0x1
    data[data.find(b"PK\x03\x04") + 6] |= 0x1
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(bytes(data))
    assert str(caught.value) == "Teleporter archive is encrypted"


def test_get_teleporter_rejects_too_many_entries(monkeypatch):
    monkeypatch.setattr(pihole_client, "TELEPORTER_MAX_ENTRIES", 2)
    data = teleporter_zip({
        "etc/pihole/pihole.toml": b"[dns]\n",
        "etc/pihole/gravity.db": b"sqlite",
        "etc/hosts": b"127.0.0.1 localhost",
    })
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(data)
    assert str(caught.value) == "Teleporter archive has too many entries"


def test_get_teleporter_rejects_oversize_uncompressed(monkeypatch):
    monkeypatch.setattr(pihole_client, "TELEPORTER_MAX_UNCOMPRESSED_BYTES", 16)
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(teleporter_zip())
    assert str(caught.value) == "Teleporter archive is too large to verify"


@pytest.mark.parametrize("missing", ["etc/pihole/pihole.toml", "etc/pihole/gravity.db"])
def test_get_teleporter_requires_members(missing):
    members = {
        "etc/pihole/pihole.toml": b'[dns]\nupstreams = ["1.1.1.1"]\n',
        "etc/pihole/gravity.db": b"sqlite",
    }
    del members[missing]
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(teleporter_zip(members))
    assert str(caught.value) == "Teleporter archive is missing a required file"


@pytest.mark.parametrize("config", [b"[dns\n", b"\xff\xfe"])
def test_get_teleporter_rejects_unparseable_toml(config):
    data = teleporter_zip({
        "etc/pihole/pihole.toml": config,
        "etc/pihole/gravity.db": b"sqlite",
    })
    with pytest.raises(pihole_client.PiholeAPIError) as caught:
        pihole_client._check_teleporter_archive(data)
    assert str(caught.value) == "Teleporter archive has an unreadable pihole.toml"


@pytest.mark.parametrize(('action', 'path'), [
    ('restart_dns', '/api/action/restartdns'),
    ('flush_logs', '/api/action/flush/logs'),
    ('flush_network', '/api/action/flush/network'),
])
def test_run_action_posts_to_ftl_action_path(monkeypatch, action, path):
    monkeypatch.setattr(pihole_client, 'resolve_instance_destination', private_destination)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'status': 'success'})

    client = pihole_client.PiholeClient('https://pihole.lan', 'secret')
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond), base_url='https://pihole.lan')
    client._session = Session('session', 'csrf')
    try:
        assert client.run_action(action) is None
    finally:
        client._client.close()
    assert len(requests) == 1
    request = requests[0]
    assert request.method == 'POST'
    assert request.url.path == path
    assert request.headers['X-FTL-SID'] == 'session'
    assert request.headers['X-FTL-CSRF'] == 'csrf'
    assert request.content == b''


def test_run_action_403_names_allow_destructive(monkeypatch):
    monkeypatch.setattr(pihole_client, 'resolve_instance_destination', private_destination)
    client = pihole_client.PiholeClient('https://pihole.lan', 'secret')
    client._client.close()
    client._client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(403, text='sensitive upstream details')),
        base_url='https://pihole.lan',
    )
    client._session = Session('session', 'csrf')
    try:
        with pytest.raises(pihole_client.PiholeAPIError) as caught:
            client.run_action('restart_dns')
        assert caught.value.status_code == 403
        assert 'webserver.api.allow_destructive' in str(caught.value)
        assert 'sensitive upstream details' not in str(caught.value)
    finally:
        client._client.close()


@pytest.mark.parametrize(('status_code', 'body'), [
    (500, b'sensitive upstream details'),
    (200, b'{"status":"error"}'),
    (200, b'not JSON'),
    (200, b'[]'),
])
def test_run_action_rejects_bad_responses(monkeypatch, status_code, body):
    monkeypatch.setattr(pihole_client, 'resolve_instance_destination', private_destination)
    client = pihole_client.PiholeClient('https://pihole.lan', 'secret')
    client._client.close()
    client._client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(status_code, content=body)),
        base_url='https://pihole.lan',
    )
    client._session = Session('session', 'csrf')
    try:
        with pytest.raises(pihole_client.PiholeAPIError) as caught:
            client.run_action('flush_logs')
        if status_code == 500:
            assert caught.value.status_code == 500
            assert str(caught.value) == 'POST /api/action/flush/logs failed with HTTP 500'
        else:
            assert str(caught.value) == 'Pi-hole action endpoint returned an invalid response'
    finally:
        client._client.close()
