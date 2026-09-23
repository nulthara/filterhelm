from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.auth import AuthConfig, AuthManager
from app.auth_middleware import AuthenticationMiddleware
from app.config import Instance
from app.pihole_client import Adlist, Client, Domain, Group, PiholeAPIError
from app.routes import api, clients, domains, groups


class FakeCredentials:
    def get(self, _name):
        return "secret"


class FakeDriftPiholeClient:
    data = {}
    fail = {}
    calls = []

    def __init__(self, base_url, *_args, **_kwargs):
        self.name = base_url.removeprefix("http://")

    def __enter__(self):
        self.calls.append((self.name, "__enter__"))
        return self

    def __exit__(self, *_args):
        self.calls.append((self.name, "__exit__"))

    def _read(self, method, default):
        self.calls.append((self.name, method))
        if (self.name, method) in self.fail:
            raise self.fail[self.name, method]
        return self.data.get(self.name, {}).get(method, default)

    def get_config(self):
        return self._read("get_config", {})

    def get_adlists(self):
        return self._read("get_adlists", [])

    def get_domains(self):
        return self._read("get_domains", [])

    def get_groups(self):
        return self._read("get_groups", [])

    def get_clients(self):
        return self._read("get_clients", [])


@pytest.fixture
def drift_setup(monkeypatch):
    from app.routes import drift

    instances = [Instance(name=name, base_url=f"http://{name}") for name in ("pihole1", "pihole2")]
    for module in (drift, api, clients, domains, groups):
        monkeypatch.setattr(module, "load_instances", lambda: instances.copy())
        monkeypatch.setattr(module, "credentials", FakeCredentials())
        monkeypatch.setattr(module, "PiholeClient", FakeDriftPiholeClient)
    monkeypatch.setattr(api, "load_pending_gravity", lambda: set())
    FakeDriftPiholeClient.data = {}
    FakeDriftPiholeClient.fail = {}
    FakeDriftPiholeClient.calls = []
    return drift, instances


def adlist(address="https://lists.example/hosts", enabled=True):
    return Adlist(address, "Lists", enabled, (0,), None, None, None)


def populated():
    return {
        "get_config": {"dns": {"hosts": ["192.168.1.10 nas.lan"], "cnameRecords": ["alias.lan,nas.lan,300"],
                               "upstreams": ["1.1.1.1", "8.8.8.8"], "queryLogging": True, "revServers": []},
                       "misc": {"privacylevel": 0}},
        "get_adlists": [adlist()],
        "get_domains": [Domain(1, "ads.example", "deny", "exact", "Comment", True, (0,), None, None)],
        "get_groups": [Group(1, "Home", "Home group", True, None, None)],
        "get_clients": [Client(1, "192.168.1.20", "Client", (0,), None, None)],
    }


def summaries(result):
    return {row["category"]: row for row in result["categories"]}


def test_drift_flags_missing_and_conflicting_entries_in_every_category(drift_setup):
    drift, _ = drift_setup
    first = populated()
    second = deepcopy(first)
    second["get_config"]["dns"]["hosts"] = ["192.168.1.11 nas.lan"]
    second["get_config"]["dns"]["cnameRecords"] = ["alias.lan,nas.lan,600"]
    second["get_config"]["dns"]["queryLogging"] = False
    second["get_adlists"] = []
    second["get_domains"] = [replace(first["get_domains"][0], enabled=False)]
    second["get_groups"] = [replace(first["get_groups"][0], enabled=False)]
    second["get_clients"] = []
    FakeDriftPiholeClient.data = {"pihole1": first, "pihole2": second}
    result = drift.drift()
    assert result["instance_names"] == result["reachable"] == ["pihole1", "pihole2"]
    assert result["comparable"] is True and result["errors"] == {}
    assert [(r["category"], r["id"], r["state"], r["values"]) for r in result["items"]] == [
        ("dns", "dns:nas.lan", "conflict", {"pihole1": "192.168.1.10", "pihole2": "192.168.1.11"}),
        ("cname", "cname:alias.lan", "conflict", {"pihole1": "nas.lan (TTL 300)", "pihole2": "nas.lan (TTL 600)"}),
        ("adlist", "adlist:https://lists.example/hosts", "missing", {"pihole1": "Enabled", "pihole2": None}),
        ("domain", "domain:deny:exact:ads.example", "conflict", {"pihole1": "Enabled", "pihole2": "Disabled"}),
        ("group", "group:Home", "conflict", {"pihole1": "Enabled", "pihole2": "Disabled"}),
        ("client", "client:192.168.1.20", "missing", {"pihole1": "Present", "pihole2": None}),
        ("setting", "setting:dns.queryLogging", "conflict", {"pihole1": "True", "pihole2": "False"}),
    ]
    assert [r["category"] for r in result["categories"]] == ["dns", "cname", "adlist", "domain", "group", "client", "setting"]
    assert result["items"][3]["detail"] == "deny · exact"
    # Feed the same data to every owning endpoint to pin the parity contract.
    for category, response, field in [
        ("dns", api.dns_records_list(), "records"), ("cname", api.dns_cnames_list(), "records"),
        ("adlist", api.adlists_list(), "adlists"), ("domain", domains.domains_list(), "domains"),
        ("group", groups.groups_list(), "groups"), ("client", clients.clients_list(), "clients"),
    ]:
        assert response[field][0]["state"] == next(r["state"] for r in result["items"] if r["category"] == category)
    assert "secret" not in str(result) and "http://pihole" not in str(result)


def test_drift_omits_consistent_entries_from_items_but_counts_them_in_totals(drift_setup):
    drift, _ = drift_setup
    FakeDriftPiholeClient.data = {name: populated() for name in ("pihole1", "pihole2")}
    result = drift.drift()
    assert result["items"] == []
    assert [r["total"] for r in result["categories"]] == [1, 1, 1, 1, 1, 1, 4]
    assert all(r["drifting"] == 0 and r["truncated"] is False for r in result["categories"])


def test_drift_conflict_outranks_missing(drift_setup):
    drift, instances = drift_setup
    instances.append(Instance(name="pihole3", base_url="http://pihole3"))
    FakeDriftPiholeClient.data = {
        "pihole1": {"get_adlists": [adlist(), adlist("https://lists.example/only")]},
        "pihole2": {"get_adlists": [adlist(enabled=False)]},
    }
    result = drift.drift()
    states = {r["key"]: r["state"] for r in result["items"]}
    assert states == {"https://lists.example/hosts": "conflict", "https://lists.example/only": "missing"}
    assert states == {r["address"]: r["state"] for r in api.adlists_list()["adlists"]}


def test_drift_dns_hostname_with_two_addresses_on_one_instance_is_a_conflict(drift_setup):
    drift, instances = drift_setup
    instances.pop()
    FakeDriftPiholeClient.data = {"pihole1": {"get_config": {"dns": {"hosts": ["192.168.1.11 nas.lan", "192.168.1.10 nas.lan"]}}}}
    result = drift.drift()
    assert result["items"] == [{"id": "dns:nas.lan", "category": "dns", "key": "nas.lan", "detail": "", "state": "conflict", "values": {"pihole1": "192.168.1.10, 192.168.1.11"}}]
    assert result["comparable"] is False


@pytest.mark.parametrize(("first", "second", "expected"), [
    (["1.1.1.1", "8.8.8.8"], ["8.8.8.8", "1.1.1.1"], ("1.1.1.1, 8.8.8.8", "8.8.8.8, 1.1.1.1")),
    (["1.1.1.1"], ["1.1.1.1"], None),
    (None, [], ("(not set)", "(empty)")),
])
def test_drift_settings_compare_raw_values_and_render_display_strings(drift_setup, first, second, expected):
    drift, _ = drift_setup
    FakeDriftPiholeClient.data = {name: {"get_config": {"dns": {"upstreams": value}}} for name, value in [("pihole1", first), ("pihole2", second)]}
    items = drift.drift()["items"]
    if expected is None:
        assert items == []
    else:
        assert len(items) == 1 and items[0]["state"] == "conflict"
        assert items[0]["id"] == "setting:dns.upstreams"
        assert items[0]["values"] == dict(zip(("pihole1", "pihole2"), expected))


def test_drift_settings_conflict_on_raw_values_that_display_identically(drift_setup):
    drift, _ = drift_setup
    FakeDriftPiholeClient.data = {"pihole1": {"get_config": {"dns": {"upstreams": []}}}, "pihole2": {"get_config": {"dns": {"upstreams": [""]}}}}
    items = drift.drift()["items"]
    assert len(items) == 1 and items[0]["state"] == "conflict"
    assert items[0]["values"] == {"pihole1": "(empty)", "pihole2": "(empty)"}


@pytest.mark.parametrize(("error", "message"), [
    (httpx.ConnectError("http://private:secret@host"), "Unable to reach Pi-hole instance"),
    (PiholeAPIError("Read failed"), "Read failed"),
    (HTTPException(409, "No credential stored"), "No credential stored"),
])
def test_drift_unreachable_instance_is_reported_and_not_counted_as_missing(drift_setup, error, message):
    drift, _ = drift_setup
    first = populated()
    first["get_config"]["dns"]["hosts"].append("192.168.1.11 nas.lan")
    FakeDriftPiholeClient.data = {"pihole1": first}
    FakeDriftPiholeClient.fail = {("pihole2", "get_config"): error}
    result = drift.drift()
    assert result["errors"] == {"pihole2": message}
    assert result["reachable"] == ["pihole1"]
    assert len(result["items"]) == 1 and result["items"][0]["category"] == "dns"
    assert set(result["items"][0]["values"]) == {"pihole1"}
    assert summaries(result)["adlist"]["drifting"] == 0


def test_drift_partial_instance_failure_drops_the_whole_instance(drift_setup):
    drift, _ = drift_setup
    FakeDriftPiholeClient.data = {"pihole2": populated()}
    FakeDriftPiholeClient.fail = {("pihole2", "get_groups"): PiholeAPIError("Groups failed")}
    result = drift.drift()
    assert result["errors"] == {"pihole2": "Groups failed"}
    assert result["reachable"] == ["pihole1"] and result["items"] == []
    assert summaries(result)["dns"]["total"] == 0
    assert ("pihole2", "__exit__") in FakeDriftPiholeClient.calls


def test_drift_single_reachable_instance_sets_comparable_false(drift_setup):
    drift, instances = drift_setup
    instances.pop()
    result = drift.drift()
    assert result["comparable"] is False
    assert result["reachable"] == ["pihole1"] and len(result["categories"]) == 7


def test_drift_caps_items_per_category_and_flags_truncation(drift_setup):
    drift, _ = drift_setup
    FakeDriftPiholeClient.data = {"pihole1": {"get_adlists": [adlist(f"https://lists.example/{n:03}") for n in reversed(range(250))]}}
    result = drift.drift()
    assert len(result["items"]) == 200
    assert result["items"][0]["key"] == "https://lists.example/000"
    assert result["items"][-1]["key"] == "https://lists.example/199"
    assert summaries(result)["adlist"] == {"category": "adlist", "label": "Block lists", "total": 250, "drifting": 250, "truncated": True}


def test_drift_reads_the_config_tree_once_per_instance(drift_setup):
    drift, _ = drift_setup
    drift.drift()
    for name in ("pihole1", "pihole2"):
        assert FakeDriftPiholeClient.calls.count((name, "get_config")) == 1


def test_drift_reads_each_instance_sequentially_inside_one_session(drift_setup):
    drift, _ = drift_setup
    drift.drift()
    for name in ("pihole1", "pihole2"):
        assert [method for instance, method in FakeDriftPiholeClient.calls if instance == name] == ["__enter__", "get_config", "get_adlists", "get_domains", "get_groups", "get_clients", "__exit__"]


def test_drift_requires_authentication(drift_setup, tmp_path):
    drift, _ = drift_setup
    manager = AuthManager(tmp_path / "auth.db", AuthConfig(mode="local", cookie_secure=False))
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware, manager=manager)
    app.include_router(drift.router)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/drift")

    response = asyncio.run(request())
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert FakeDriftPiholeClient.calls == []


def test_drift_with_no_configured_instances_returns_empty_categories(drift_setup):
    drift, instances = drift_setup
    instances.clear()
    result = drift.drift()
    assert result["comparable"] is False and result["items"] == []
    assert result["reachable"] == result["instance_names"] == [] and result["errors"] == {}
    assert len(result["categories"]) == 7
    assert all(r["total"] == r["drifting"] == 0 and r["truncated"] is False for r in result["categories"])


def test_drift_preserves_raw_adlist_keys_and_domain_identity(drift_setup):
    drift, _ = drift_setup
    first = populated()
    second = deepcopy(first)
    second["get_adlists"] = [adlist("https://lists.example/hosts/")]
    second["get_domains"] = [replace(first["get_domains"][0], type="allow")]
    FakeDriftPiholeClient.data = {"pihole1": first, "pihole2": second}
    items = drift.drift()["items"]
    assert {r["id"] for r in items} == {"adlist:https://lists.example/hosts", "adlist:https://lists.example/hosts/", "domain:deny:exact:ads.example", "domain:allow:exact:ads.example"}
    assert all(r["state"] == "missing" for r in items)
