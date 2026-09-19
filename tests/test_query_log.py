from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from fastapi import HTTPException

from app.config import Instance
from app.pihole_client import (
    BLOCKED_STATUSES, QUERY_STATUSES, PiholeAPIError, QueryPage, QueryRecord,
    StatsSummary, TopClient, TopClients, TopDomain, TopDomains, Upstream, Upstreams,
)
from app.routes import query_log


class FakeCredentials:
    def get(self, _name):
        return "secret"


class FakeQueryPiholeClient:
    summaries: dict[str, StatsSummary] = {}
    top_domains: dict[str, dict[bool, TopDomains]] = {}
    top_clients: dict[str, dict[bool, TopClients]] = {}
    upstreams: dict[str, Upstreams] = {}
    pages: dict[str, dict[str | None, QueryPage]] = {}
    fail: dict[str, Exception] = {}
    fail_on: dict[str, str] = {}
    calls: list[tuple[str, str, dict]] = []

    def __init__(self, base_url, *_args, **_kwargs):
        self.name = base_url.removeprefix("http://")

    def __enter__(self):
        self.calls.append((self.name, "enter", {}))
        return self

    def __exit__(self, *_args):
        self.calls.append((self.name, "exit", {}))

    def _read(self, method, **kwargs):
        self.calls.append((self.name, method, kwargs))
        if self.name in self.fail and self.fail_on.get(self.name, method) == method:
            raise self.fail[self.name]

    def get_stats_summary(self):
        self._read("summary")
        return self.summaries.get(self.name, StatsSummary(0, 0, 0.0, 0, 0, 0, 0, 0, 0, 0))

    def get_top_domains(self, *, blocked, count):
        self._read("top_domains", blocked=blocked, count=count)
        return self.top_domains.get(self.name, {}).get(blocked, TopDomains((), 0, 0))

    def get_top_clients(self, *, blocked, count):
        self._read("top_clients", blocked=blocked, count=count)
        return self.top_clients.get(self.name, {}).get(blocked, TopClients((), 0, 0))

    def get_upstreams(self):
        self._read("upstreams")
        return self.upstreams.get(self.name, Upstreams((), 0, 0))

    def get_queries(self, **kwargs):
        self._read("queries", **kwargs)
        return self.pages.get(self.name, {}).get(kwargs["status"], QueryPage((), 0, 0, None))


@pytest.fixture(autouse=True)
def setup_query_log(monkeypatch):
    instances = [Instance(name=n, base_url=f"http://{n}") for n in ("pihole1", "pihole2")]
    monkeypatch.setattr(query_log, "load_instances", lambda: instances.copy())
    monkeypatch.setattr(query_log, "credentials", FakeCredentials())
    monkeypatch.setattr(query_log, "PiholeClient", FakeQueryPiholeClient)
    monkeypatch.setattr(query_log, "_now", lambda: 10_000.0)
    for attr in ("summaries", "top_domains", "top_clients", "upstreams", "pages", "fail", "fail_on"):
        setattr(FakeQueryPiholeClient, attr, {})
    FakeQueryPiholeClient.calls = []


def row(id=1, time=30, status="GRAVITY"):
    return QueryRecord(id, time, "A", "ads.example", None, status, "192.168.1.5", None, None, "IP", 0.001)


def page(*rows, total=10, earliest=100):
    return QueryPage(tuple(rows), 100, total, earliest)


def query_calls(name="pihole1"):
    return [kwargs for n, method, kwargs in FakeQueryPiholeClient.calls if n == name and method == "queries"]


def lab_stats():
    FakeQueryPiholeClient.summaries["pihole1"] = StatsSummary(156, 0, 0, 42, 100, 56, 2, 2, 79541, 1788845689)
    FakeQueryPiholeClient.top_domains["pihole1"] = {False: TopDomains((TopDomain("1.in-addr.arpa", 25), TopDomain("1.0.0.127.in-addr.arpa", 24), TopDomain("2.0.0.1.in-addr.arpa", 18)), 156, 0)}
    FakeQueryPiholeClient.top_clients["pihole1"] = {False: TopClients((TopClient("::", "pi.hole", 97), TopClient("127.0.0.1", "localhost.lan", 59)), 156, 0)}
    FakeQueryPiholeClient.upstreams["pihole1"] = Upstreams((Upstream("blocklist", "blocklist", -1, 0, 0, 0), Upstream("cache", "cache", -1, 56, 0, 0), Upstream("1.0.0.2", "security.cloudflare-dns.com", 53, 62, 0.020752204044927818, 0.0007558185700903987), Upstream("1.1.1.2", "security.cloudflare-dns.com", 53, 38, 0.01903403675095718, 0.0006069763137027048)), 156, 100)


def test_blocked_statuses_match_ftl_is_blocked():
    assert BLOCKED_STATUSES == {"GRAVITY", "REGEX", "DENYLIST", "EXTERNAL_BLOCKED_IP", "EXTERNAL_BLOCKED_NULL", "EXTERNAL_BLOCKED_NXRA", "EXTERNAL_BLOCKED_EDE15", "GRAVITY_CNAME", "REGEX_CNAME", "DENYLIST_CNAME", "DBBUSY", "SPECIAL_DOMAIN"}
    assert BLOCKED_STATUSES <= set(QUERY_STATUSES)


def test_query_log_fans_out_and_isolates_instance_errors():
    lab_stats()
    FakeQueryPiholeClient.pages["pihole1"] = {None: page(row())}
    FakeQueryPiholeClient.fail["pihole2"] = PiholeAPIError("stats failed")
    result = query_log.query_log()
    assert result["instance_names"] == ["pihole1", "pihole2"]
    assert result["errors"] == {"pihole2": "stats failed"}
    assert set(result["instances"]) == {"pihole1"}
    assert result["instances"]["pihole1"]["summary"]["total"] == 156
    assert result["instances"]["pihole1"]["top_domains"]["permitted"][0] == {"domain": "1.in-addr.arpa", "count": 25}
    assert len(result["queries"]) == 1
    assert result["combined"]["instances_reporting"] == 1


def test_any_failed_stat_read_fails_that_instance_only():
    FakeQueryPiholeClient.fail["pihole1"] = PiholeAPIError("upstreams failed")
    FakeQueryPiholeClient.fail_on["pihole1"] = "upstreams"
    FakeQueryPiholeClient.pages = {n: {None: page(row())} for n in ("pihole1", "pihole2")}
    result = query_log.query_log()
    assert result["errors"] == {"pihole1": "upstreams failed"}
    assert set(result["instances"]) == {"pihole2"}
    assert [r["instance"] for r in result["queries"]] == ["pihole2"]
    assert query_calls() == []


def test_query_log_unreachable_instance_hides_transport_detail():
    FakeQueryPiholeClient.fail["pihole1"] = httpx.ConnectError("private URL secret")
    result = query_log.query_log()
    assert result["errors"] == {"pihole1": "Unable to reach Pi-hole instance"}


def test_reads_happen_in_order_inside_one_session():
    query_log.query_log()
    for name in ("pihole1", "pihole2"):
        assert [(method, kwargs) for n, method, kwargs in FakeQueryPiholeClient.calls if n == name] == [
            ("enter", {}), ("summary", {}), ("top_domains", {"blocked": False, "count": 10}),
            ("top_domains", {"blocked": True, "count": 10}), ("top_clients", {"blocked": False, "count": 10}),
            ("top_clients", {"blocked": True, "count": 10}), ("upstreams", {}),
            ("queries", {"start": 6400.0, "length": 100, "domain": None, "client_ip": None, "client_name": None, "status": None}), ("exit", {}),
        ]


def test_stats_are_serialized_per_instance():
    lab_stats()
    result = query_log.query_log()
    assert result["top_count"] == 10
    assert result["instances"]["pihole1"] == {
        "summary": {"total": 156, "blocked": 0, "percent_blocked": 0, "unique_domains": 42, "forwarded": 100, "cached": 56, "active_clients": 2, "total_clients": 2, "gravity_domains": 79541, "gravity_last_update": 1788845689},
        "top_domains": {"permitted": [{"domain": "1.in-addr.arpa", "count": 25}, {"domain": "1.0.0.127.in-addr.arpa", "count": 24}, {"domain": "2.0.0.1.in-addr.arpa", "count": 18}], "blocked": []},
        "top_clients": {"permitted": [{"ip": "::", "name": "pi.hole", "count": 97}, {"ip": "127.0.0.1", "name": "localhost.lan", "count": 59}], "blocked": []},
        "upstreams": [{"ip": "blocklist", "name": "blocklist", "port": -1, "count": 0, "response": 0, "variance": 0}, {"ip": "cache", "name": "cache", "port": -1, "count": 56, "response": 0, "variance": 0}, {"ip": "1.0.0.2", "name": "security.cloudflare-dns.com", "port": 53, "count": 62, "response": 0.020752204044927818, "variance": 0.0007558185700903987}, {"ip": "1.1.1.2", "name": "security.cloudflare-dns.com", "port": 53, "count": 38, "response": 0.01903403675095718, "variance": 0.0006069763137027048}],
        "records_filtered": 0, "returned": 0, "earliest_timestamp": None,
    }


def test_rows_merge_newest_first_across_instances():
    FakeQueryPiholeClient.pages = {"pihole1": {None: page(row(2, 30), row(1, 10, "CACHE"))}, "pihole2": {None: page(row(1, 20, None), row(3, 30, "DBBUSY"))}}
    rows = query_log.query_log()["queries"]
    assert [(r["instance"], r["time"], r["id"], r["blocked"]) for r in rows] == [("pihole2", 30, 3, True), ("pihole1", 30, 2, True), ("pihole2", 20, 1, False), ("pihole1", 10, 1, False)]
    assert rows[1] == {"instance": "pihole1", "id": 2, "time": 30, "type": "A", "domain": "ads.example", "cname": None, "status": "GRAVITY", "blocked": True, "client_ip": "192.168.1.5", "client_name": None, "upstream": None, "reply_type": "IP", "reply_time": 0.001}


def test_blocked_filter_issues_one_call_per_blocked_status():
    FakeQueryPiholeClient.pages["pihole1"] = {"DBBUSY": page(total=5, earliest=42), "GRAVITY": page(row(1, 10), row(2, 30), total=7), "REGEX": page(row(3, 20, "REGEX"), row(4, 40, "REGEX"), total=9)}
    result = query_log.query_log(status="blocked", length=3)
    assert [c["status"] for c in query_calls()] == sorted(BLOCKED_STATUSES)
    assert [r["id"] for r in result["queries"]] == [4, 2, 3]
    assert result["instances"]["pihole1"]["records_filtered"] == 21
    assert result["instances"]["pihole1"]["returned"] == 3
    assert result["instances"]["pihole1"]["earliest_timestamp"] == 42


@pytest.mark.parametrize(("value", "expected"), [("cache_stale", "CACHE_STALE"), ("", None)])
def test_specific_status_is_uppercased_and_passed_through(value, expected):
    result = query_log.query_log(status=value)
    assert [c["status"] for c in query_calls()] == [expected]
    assert result["status"] == (expected or "")


def test_status_validation():
    with pytest.raises(HTTPException) as caught:
        query_log.query_log(status="BOGUS")
    assert caught.value.status_code == 422
    assert "Unknown query status" in caught.value.detail


@pytest.mark.parametrize("kwargs", [{"window": 59}, {"window": 86401}, {"length": 0}, {"length": 501}])
def test_window_and_length_bounds(kwargs):
    with pytest.raises(HTTPException) as caught:
        query_log.query_log(**kwargs)
    assert caught.value.status_code == 422
    result = query_log.query_log()
    assert (result["window"], result["length"], result["since"]) == (3600, 100, 6400.0)
    assert query_calls()[0]["length"] == 100
    assert query_calls()[0]["start"] == 6400.0


@pytest.mark.parametrize(("value", "expected"), [("192.168.1.5", ("192.168.1.5", None)), ("192.168.*", ("192.168.*", None)), ("fe80::1", ("fe80::1", None)), ("::", ("::", None)), ("nas", (None, "nas")), ("nas.lan", (None, "nas.lan")), ("*phone*", (None, "*phone*")), ("", (None, None))])
def test_client_filter_routes_ip_and_name(value, expected):
    assert query_log._client_filter(value) == expected
    query_log.query_log(client=value)
    assert (query_calls()[0]["client_ip"], query_calls()[0]["client_name"]) == expected


@pytest.mark.parametrize("kwargs", [{"domain": "a b"}, {"domain": "a/b"}, {"client": "a b"}, {"client": "a/b"}])
def test_domain_and_client_filters_are_normalized_and_validated(kwargs):
    with pytest.raises(HTTPException) as caught:
        query_log.query_log(**kwargs)
    assert caught.value.status_code == 422
    result = query_log.query_log(domain=" *.Example.COM ", client=" nas.lan ")
    assert result["domain"] == "*.example.com"
    assert result["client"] == "nas.lan"
    assert query_calls()[0]["domain"] == "*.example.com"
    query_log.query_log()
    assert query_calls()[-1]["domain"] is None


def test_combined_counts_reporting_instances_only():
    zero = StatsSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    FakeQueryPiholeClient.summaries = {"pihole1": replace(zero, total=100, blocked=20), "pihole2": replace(zero, total=300, blocked=30)}
    assert query_log.query_log()["combined"] == {"total": 400, "blocked": 50, "percent_blocked": 12.5, "instances_reporting": 2}
    FakeQueryPiholeClient.fail["pihole2"] = PiholeAPIError("failed")
    assert query_log.query_log()["combined"] == {"total": 100, "blocked": 20, "percent_blocked": 20.0, "instances_reporting": 1}
    FakeQueryPiholeClient.fail = {}
    FakeQueryPiholeClient.summaries = {"pihole1": zero, "pihole2": zero}
    assert query_log.query_log()["combined"]["percent_blocked"] == 0.0
    FakeQueryPiholeClient.fail = {n: PiholeAPIError("failed") for n in ("pihole1", "pihole2")}
    assert query_log.query_log()["combined"] is None


def test_query_log_module_has_no_audit_path():
    assert not hasattr(query_log, "record")


def test_query_log_requires_authentication(tmp_path):
    import asyncio

    from fastapi import FastAPI

    from app.auth import AuthConfig, AuthManager
    from app.auth_middleware import AuthenticationMiddleware

    manager = AuthManager(tmp_path / "auth.db", AuthConfig(mode="local", cookie_secure=False))
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware, manager=manager)
    app.include_router(query_log.router)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/query-log")

    response = asyncio.run(request())
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert FakeQueryPiholeClient.calls == []
