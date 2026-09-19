from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from fastapi import HTTPException

from app.config import Instance
from app.pihole_client import Domain, GravityMatch, PiholeAPIError, SearchCounts, SearchResult
from app.routes import lookup


class FakeCredentials:
    def get(self, _name):
        return "secret"


class FakeLookupPiholeClient:
    results: dict[str, SearchResult] = {}
    fail: dict[str, Exception] = {}
    calls: list[tuple[str, str, bool, int]] = []

    def __init__(self, base_url, *_args, **_kwargs):
        self.name = base_url.removeprefix("http://")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def search_domain(self, term, *, partial=False, limit=20):
        self.calls.append((self.name, term, partial, limit))
        if self.name in self.fail:
            raise self.fail[self.name]
        return self.results.get(self.name, SearchResult((), (), SearchCounts(0, 0, 0, 0, 0)))


@pytest.fixture(autouse=True)
def setup_lookup(monkeypatch):
    instances = [Instance(name=name, base_url=f"http://{name}") for name in ("pihole1", "pihole2")]
    monkeypatch.setattr(lookup, "load_instances", lambda: instances.copy())
    monkeypatch.setattr(lookup, "credentials", FakeCredentials())
    monkeypatch.setattr(lookup, "PiholeClient", FakeLookupPiholeClient)
    FakeLookupPiholeClient.results = {}
    FakeLookupPiholeClient.fail = {}
    FakeLookupPiholeClient.calls = []


def domain(type="deny", kind="exact", enabled=True):
    return Domain(1, "ads.example", type, kind, "Override", enabled, (0, 2), None, None)


def gravity(type="block", enabled=True):
    return GravityMatch(2, "ads.example", "https://lists.example/hosts", type, "Hosts", enabled, (0,))


def matches(domains=(), gravity=(), *, total=None):
    counts = SearchCounts(
        sum(d.kind == "exact" for d in domains), sum(d.kind == "regex" for d in domains),
        sum(g.type == "allow" for g in gravity), sum(g.type == "block" for g in gravity),
        len(domains) + len(gravity) if total is None else total,
    )
    return SearchResult(tuple(domains), tuple(gravity), counts)


def test_lookup_fans_out_and_isolates_instance_errors():
    FakeLookupPiholeClient.results["pihole1"] = matches([domain("allow")], [gravity()])
    FakeLookupPiholeClient.fail["pihole2"] = PiholeAPIError("search failed")
    result = lookup.lookup("ads.example")
    assert result["instance_names"] == ["pihole1", "pihole2"]
    assert result["errors"] == {"pihole2": "search failed"}
    assert set(result["instances"]) == {"pihole1"}
    assert result["instances"]["pihole1"] == {
        "verdict": "allowed", "reason": "Exact allow entry ads.example",
        "domains": [{"domain": "ads.example", "type": "allow", "kind": "exact", "comment": "Override", "enabled": True, "groups": [0, 2]}],
        "gravity": [{"domain": "ads.example", "address": "https://lists.example/hosts", "type": "block", "comment": "Hosts", "enabled": True, "groups": [0]}],
        "counts": {"domains_exact": 1, "domains_regex": 0, "gravity_allow": 0, "gravity_block": 1, "total": 2},
        "truncated": False,
    }


def test_lookup_unreachable_instance_hides_transport_detail():
    FakeLookupPiholeClient.fail["pihole1"] = httpx.ConnectError("private transport details")
    result = lookup.lookup("ads.example")
    assert result["errors"] == {"pihole1": "Unable to reach Pi-hole instance"}
    assert "pihole1" not in result["instances"]


def test_malformed_counts_become_an_instance_error_not_a_verdict():
    FakeLookupPiholeClient.fail["pihole1"] = PiholeAPIError("Pi-hole search endpoint returned invalid result counts")
    result = lookup.lookup("ads.example")
    assert result["errors"] == {"pihole1": "Pi-hole search endpoint returned invalid result counts"}
    assert "pihole1" not in result["instances"]


@pytest.mark.parametrize(("domains", "gravity_rows", "expected"), [
    ([domain("allow"), domain(), domain("allow", "regex"), domain("deny", "regex")], [gravity("allow"), gravity()], ("allowed", "Exact allow entry ads.example")),
    ([domain(), domain("allow", "regex"), domain("deny", "regex")], [gravity("allow"), gravity()], ("allowed", "Allow list https://lists.example/hosts")),
    ([domain(), domain("allow", "regex"), domain("deny", "regex")], [gravity()], ("blocked", "Exact deny entry ads.example")),
    ([domain("allow", "regex"), domain("deny", "regex")], [gravity()], ("allowed", "Regex allow entry ads.example")),
    ([domain("deny", "regex")], [gravity()], ("blocked", "Block list https://lists.example/hosts")),
    ([domain("deny", "regex")], [], ("blocked", "Regex deny entry ads.example")),
    ([], [], ("unlisted", "No enabled entry matches this domain")),
])
def test_verdict_precedence(domains, gravity_rows, expected):
    assert lookup._verdict(matches(domains, gravity_rows)) == expected


@pytest.mark.parametrize("has_block", [False, True])
def test_verdict_ignores_disabled_entries(has_block):
    FakeLookupPiholeClient.results["pihole1"] = matches([domain(enabled=False)], [gravity()] if has_block else [])
    result = lookup.lookup("ads.example")["instances"]["pihole1"]
    assert (result["verdict"], result["reason"]) == (
        ("blocked", "Block list https://lists.example/hosts") if has_block
        else ("unlisted", "No enabled entry matches this domain")
    )
    assert len(result["domains"]) == 1
    assert result["domains"][0]["enabled"] is False
    assert lookup._verdict(matches(gravity=[gravity("allow", enabled=False)])) == ("unlisted", "No enabled entry matches this domain")


def test_partial_lookup_has_no_verdict():
    FakeLookupPiholeClient.results["pihole1"] = matches([domain()])
    response = lookup.lookup("ads", partial=True)
    result = response["instances"]["pihole1"]
    assert result["verdict"] is None and result["reason"] is None
    assert result["truncated"] is False
    assert response["partial"] is True
    assert ("pihole1", "ads", True, 100) in FakeLookupPiholeClient.calls


@pytest.mark.parametrize(("partial", "limit"), [(False, 1000), (True, 100)])
def test_lookup_limit_depends_on_match_mode(partial, limit):
    response = lookup.lookup("ads.example", partial=partial)
    assert response["limit"] == limit
    assert sorted(FakeLookupPiholeClient.calls) == [(name, "ads.example", partial, limit) for name in ("pihole1", "pihole2")]


@pytest.mark.parametrize(("total", "truncated"), [(2, False), (3, True)])
def test_lookup_flags_truncation(total, truncated):
    FakeLookupPiholeClient.results["pihole1"] = matches([domain()], [gravity()], total=total)
    result = lookup.lookup("ads", partial=True)["instances"]["pihole1"]
    assert result["truncated"] is truncated


@pytest.mark.parametrize(("total", "verdict", "reason"), [(2, None, None), (1, "blocked", "Block list https://lists.example/hosts")])
def test_truncated_exact_search_withholds_the_verdict(total, verdict, reason):
    result = matches(gravity=[gravity()])
    if total == 2:
        result = replace(result, counts=SearchCounts(1, 0, 0, 1, 2))
    FakeLookupPiholeClient.results["pihole1"] = result
    assert lookup._verdict(result)[0] == "blocked"
    response = lookup.lookup("ads.example")["instances"]["pihole1"]
    assert response["verdict"] == verdict
    assert response["reason"] == reason
    assert response["truncated"] is (total == 2)


@pytest.mark.parametrize("term", ["", "   ", "a b", "a/b", "a" * 254])
def test_lookup_normalizes_and_validates_term(term):
    response = lookup.lookup(" Ads.Example. ")
    assert response["term"] == "ads.example"
    assert all(call[1] == "ads.example" for call in FakeLookupPiholeClient.calls)
    with pytest.raises(HTTPException) as caught:
        lookup.lookup(term)
    assert caught.value.status_code == 422
    assert isinstance(caught.value.detail, str) and caught.value.detail


def test_lookup_module_has_no_audit_path():
    assert not hasattr(lookup, "record")
