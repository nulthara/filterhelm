from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Instance
from app.pihole_client import Domain, PiholeAPIError
from app.routes import domains


class FakeCredentials:
    def get(self, _name):
        return "secret"


class FakeDomainsPiholeClient:
    unreachable: set[str] = set()
    transport_fail_add: set[str] = set()
    fail_write: dict[str, Exception] = {}
    audits = []
    items: dict[str, list[Domain]] = {}
    added = []
    updated = []
    fail_add: set[str] = set()

    def __init__(self, base_url, *_args, **_kwargs):
        self.name = base_url.removeprefix("http://")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def get_domains(self):
        if self.name in self.unreachable:
            raise httpx.ConnectError("boom")
        return self.items.get(self.name, []).copy()

    def add_domain(self, domain, type, kind, comment=""):
        if self.name in self.transport_fail_add:
            raise httpx.ConnectError("boom")
        if self.name in self.fail_add:
            raise PiholeAPIError("domain add failed")
        self.added.append((self.name, domain, type, kind, comment))
        self.items.setdefault(self.name, []).append(
            Domain(None, domain, type, kind, comment, True, (0,), None, None)
        )

    def update_domain(self, item, *, enabled, groups):
        if self.name in self.fail_write:
            raise self.fail_write[self.name]
        self.updated.append((self.name, item, enabled, groups))
        self.items[self.name] = [
            Domain(
                current.id,
                current.domain,
                current.type,
                current.kind,
                current.comment,
                enabled,
                tuple(groups),
                current.date_added,
                current.date_modified,
            )
            if current == item else current
            for current in self.items.get(self.name, [])
        ]

    def delete_domain(self, domain, type, kind):
        if self.name in self.fail_write:
            raise self.fail_write[self.name]
        self.items[self.name] = [
            item for item in self.items.get(self.name, [])
            if (item.domain, item.type, item.kind) != (domain, type, kind)
        ]


def make_domain(
    domain: str,
    type: str = "deny",
    kind: str = "exact",
    *,
    enabled: bool = True,
    comment: str = "",
) -> Domain:
    return Domain(None, domain, type, kind, comment, enabled, (0,), None, None)


def setup_domains(monkeypatch):
    FakeDomainsPiholeClient.unreachable = set()
    FakeDomainsPiholeClient.transport_fail_add = set()
    FakeDomainsPiholeClient.fail_write = {}
    FakeDomainsPiholeClient.audits = []
    instances = [
        Instance(name="pihole1", base_url="http://pihole1"),
        Instance(name="pihole2", base_url="http://pihole2"),
    ]
    monkeypatch.setattr(domains, "load_instances", lambda: instances.copy())
    monkeypatch.setattr(domains, "credentials", FakeCredentials())
    monkeypatch.setattr(domains, "PiholeClient", FakeDomainsPiholeClient)
    monkeypatch.setattr(domains, "record", lambda *args: FakeDomainsPiholeClient.audits.append(args))
    FakeDomainsPiholeClient.items = {}
    FakeDomainsPiholeClient.added = []
    FakeDomainsPiholeClient.updated = []
    FakeDomainsPiholeClient.fail_add = set()
    return instances


def test_domains_are_returned_in_unified_view(monkeypatch):
    setup_domains(monkeypatch)
    FakeDomainsPiholeClient.items = {
        "pihole1": [
            make_domain("consistent.example"),
            make_domain("missing.example"),
            make_domain("conflict.example", enabled=True),
        ],
        "pihole2": [
            make_domain("consistent.example"),
            make_domain("conflict.example", enabled=False),
        ],
    }

    result = domains.domains_list()
    rows = {row["domain"]: row for row in result["domains"]}

    assert result["instance_names"] == ["pihole1", "pihole2"]
    assert rows["consistent.example"]["state"] == "consistent"
    assert rows["missing.example"]["state"] == "missing"
    assert rows["conflict.example"]["state"] == "conflict"


def test_domain_add_reports_per_instance_results(monkeypatch):
    setup_domains(monkeypatch)
    FakeDomainsPiholeClient.fail_add = {"pihole2"}

    result = domains.domain_add(
        domains.DomainAddInput(
            instances=["pihole1", "pihole2"],
            domain="ads.example.com",
            type="deny",
            kind="exact",
            comment="New",
        )
    )

    assert [item["success"] for item in result["results"]] == [True, False]
    assert result["results"][1]["error"] == "domain add failed"
    assert FakeDomainsPiholeClient.added == [
        ("pihole1", "ads.example.com", "deny", "exact", "New")
    ]


def test_domain_update_toggles_enabled_and_preserves_comment(monkeypatch):
    setup_domains(monkeypatch)
    original = make_domain("ads.example.com", comment="existing")
    FakeDomainsPiholeClient.items = {"pihole1": [original]}

    result = domains.domain_update(
        "pihole1",
        domains.DomainUpdateInput(
            domain="ads.example.com", type="deny", kind="exact", enabled=False
        ),
    )

    assert result["enabled"] is False
    assert FakeDomainsPiholeClient.updated == [("pihole1", original, False, [0])]
    assert FakeDomainsPiholeClient.updated[0][1].comment == "existing"


def test_domain_delete_removes_by_type_kind_domain(monkeypatch):
    setup_domains(monkeypatch)
    deny = make_domain("ads.example.com", "deny", "exact")
    allow_regex = make_domain("ads.example.com", "allow", "regex")
    FakeDomainsPiholeClient.items = {"pihole1": [deny, allow_regex]}

    domains.domain_delete(
        "pihole1",
        domains.DomainDeleteInput(domain="ads.example.com", type="deny", kind="exact"),
    )

    assert FakeDomainsPiholeClient.items["pihole1"] == [allow_regex]


def test_domain_delete_uses_stored_form_not_submitted_case(monkeypatch):
    # _find_domain matches case-/trailing-dot-insensitively, so a delete
    # request submitted in a different case than what's stored must still
    # send the *stored* value to the client — not the submitted one, which
    # Pi-hole may not recognize.
    setup_domains(monkeypatch)
    stored = make_domain("Ads.Example.com", "deny", "exact")
    FakeDomainsPiholeClient.items = {"pihole1": [stored]}

    domains.domain_delete(
        "pihole1",
        domains.DomainDeleteInput(domain="ads.example.com.", type="deny", kind="exact"),
    )

    assert FakeDomainsPiholeClient.items["pihole1"] == []


def test_domain_exact_kind_rejects_invalid_hostname():
    with pytest.raises(ValidationError):
        domains.DomainAddInput(
            instances=["pihole1"], domain="not a host!", type="deny", kind="exact"
        )


def test_domain_regex_kind_accepts_non_hostname_pattern(monkeypatch):
    setup_domains(monkeypatch)
    pattern = r"^ads[0-9]+\.example\.com$"

    result = domains.domain_add(
        domains.DomainAddInput(
            instances=["pihole1"], domain=pattern, type="deny", kind="regex"
        )
    )

    assert result["results"][0]["success"] is True
    assert FakeDomainsPiholeClient.added[0][1] == pattern


@pytest.mark.parametrize("kind", ["exact", "regex"])
def test_domain_regex_kind_rejects_control_characters(kind):
    with pytest.raises(ValidationError):
        domains.DomainAddInput(
            instances=["pihole1"], domain="ads\x00.example", type="deny", kind=kind
        )


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_domain_not_found_on_instance_returns_404(monkeypatch, operation):
    setup_domains(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        if operation == "update":
            domains.domain_update(
                "pihole1",
                domains.DomainUpdateInput(
                    domain="missing.example", type="deny", kind="exact", enabled=False
                ),
            )
        else:
            domains.domain_delete(
                "pihole1",
                domains.DomainDeleteInput(
                    domain="missing.example", type="deny", kind="exact"
                ),
            )
    assert caught.value.status_code == 404


@pytest.mark.parametrize("before_enabled", [True, False])
@pytest.mark.parametrize(
    ("extra", "replacement_enabled", "expected"),
    [({"groups": [0, 5]}, None, [0, 5]),
     ({"enabled": False}, False, [0, 2]),
     ({"enabled": True}, True, [0, 2]),
     ({"groups": []}, None, []),
     ({"enabled": None, "groups": [0, 5]}, None, [0, 5]),
     ({"enabled": False, "groups": None}, False, [0, 2]),
     ({"enabled": False, "groups": [0, 5]}, False, [0, 5]),
     ({"enabled": True, "groups": []}, True, [])],
    ids=["replaces_groups_and_preserves_enabled", "replaces_enabled_and_preserves_groups",
         "enables_and_preserves_groups", "clears_groups", "null_enabled_preserves_existing",
         "null_groups_preserves_existing", "replaces_both", "enables_and_clears_groups"],
)
def test_domain_update_group_targeting(monkeypatch, extra, replacement_enabled, expected, before_enabled):
    setup_domains(monkeypatch)
    enabled = before_enabled if replacement_enabled is None else replacement_enabled
    original = Domain(1, "ads.example.com", "deny", "exact", "Existing", before_enabled, (0, 2), None, None)
    FakeDomainsPiholeClient.items = {"pihole1": [original], "pihole2": [original]}
    items = FakeDomainsPiholeClient.items
    calls = FakeDomainsPiholeClient.updated
    payload = {"domain": original.domain, "type": original.type, "kind": original.kind, **extra}
    from app.routes import api
    pending = {"pihole2"}
    monkeypatch.setattr(api, "load_pending_gravity", lambda: pending.copy())
    monkeypatch.setattr(api, "mark_gravity_pending", pending.add)
    audit = []
    monkeypatch.setattr(domains, "record", lambda *args: audit.append(args))
    result = domains.domain_update("pihole1", domains.DomainUpdateInput(**payload))
    assert result["groups"] == expected
    assert result["enabled"] is enabled
    assert calls == [("pihole1", original, enabled, expected)]
    assert items["pihole1"][0].groups == tuple(expected)
    assert items["pihole2"] == [original]
    assert audit[0][0:2] == ("pihole1", "domain_update")
    assert audit[0][2]["groups"] == [0, 2]
    assert audit[0][3]["groups"] == expected
    assert audit[0][3]["enabled"] is enabled
    assert result == {"instance": "pihole1", "domain": original.domain,
                               "type": "deny", "kind": "exact", "enabled": enabled, "groups": expected}
    assert api.load_pending_gravity() == {"pihole2"}


@pytest.mark.parametrize("groups", [[True], ["5"], [5.0], [-1], list(range(101))])
def test_domain_update_rejects_invalid_groups(groups):
    with pytest.raises(ValidationError):
        domains.DomainUpdateInput(domain="ads.example.com", type="deny", kind="exact", enabled=True, groups=groups)


@pytest.mark.parametrize("extra", [{}, {"enabled": None}, {"groups": None}, {"enabled": None, "groups": None}])
def test_domain_update_rejects_empty_payload(monkeypatch, extra):
    import asyncio

    import httpx
    from fastapi import FastAPI

    setup_domains(monkeypatch)
    def unexpected_client(*_args):
        pytest.fail("An empty update must not access Pi-hole")
    monkeypatch.setattr(domains, "_client", unexpected_client)
    test_app = FastAPI()
    test_app.include_router(domains.router)

    async def patch():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url="http://test") as client:
            return await client.patch("/api/domains/pihole1", json={**{"domain": "ads.example.com", "type": "deny", "kind": "exact"}, **extra})

    response = asyncio.run(patch())
    assert response.status_code == 422
    assert "Provide enabled or groups" in response.text


@pytest.fixture
def domain_bulk_client(monkeypatch):
    setup_domains(monkeypatch)

    class BulkClient(FakeDomainsPiholeClient):
        constructed = []
        reads = []
        deleted = []
        fail_updates = set()

        def __init__(self, base_url, *_args, **_kwargs):
            super().__init__(base_url)
            self.constructed.append(base_url)

        def get_domains(self):
            self.reads.append(self.name)
            return super().get_domains()

        def update_domain(self, item, *, enabled, groups):
            if item.domain in self.fail_updates:
                self.updated.append((self.name, item, enabled, groups))
                raise PiholeAPIError("update failed")
            super().update_domain(item, enabled=enabled, groups=groups)

        def delete_domain(self, domain, type, kind):
            self.deleted.append((self.name, domain, type, kind))
            super().delete_domain(domain, type, kind)

    monkeypatch.setattr(domains, "PiholeClient", BulkClient)
    return BulkClient


def _domain_batch(action: str, refs: list[tuple[str, str, str]]) -> dict:
    return domains.domain_bulk("pihole1", domains.DomainBulkInput(
        action=action,
        domains=[domains.DomainRef(domain=domain, type=type, kind=kind) for domain, type, kind in refs],
    ))


def test_domain_bulk_enable_writes_only_changed_records(domain_bulk_client, monkeypatch):
    items = [make_domain(f"{n}.example", enabled=n == 2) for n in range(3)]
    domain_bulk_client.items = {"pihole1": items}
    audits = []
    monkeypatch.setattr(domains, "record", lambda *args: audits.append(args))
    result = _domain_batch("enable", [(item.domain, item.type, item.kind) for item in items])
    assert domain_bulk_client.updated == [("pihole1", items[0], True, [0]), ("pihole1", items[1], True, [0])]
    assert [item["changed"] for item in result["results"]] == [True, True, False]
    assert all(item["success"] for item in result["results"])
    assert domain_bulk_client.constructed == ["http://pihole1"]
    assert domain_bulk_client.reads == ["pihole1"]
    assert len(audits) == 2
    assert all(entry[1] == "domain_update" and entry[3]["bulk"] == "enable" for entry in audits)


def test_domain_bulk_delete_matches_case_insensitively(domain_bulk_client):
    domain_bulk_client.items = {"pihole1": [make_domain("Ads.Example.COM")]}
    result = _domain_batch("delete", [("ads.example.com", "deny", "exact")])
    assert domain_bulk_client.deleted == [("pihole1", "Ads.Example.COM", "deny", "exact")]
    assert result["results"][0]["id"] == "deny:exact:ads.example.com"
    assert result["results"][0]["changed"] is True


def test_domain_bulk_does_not_mark_gravity(domain_bulk_client, monkeypatch):
    from app.routes import api
    from app import config

    def unexpected_gravity(_name):
        pytest.fail("Domain bulk must not mark gravity")

    monkeypatch.setattr(api, "mark_gravity_pending", unexpected_gravity)
    monkeypatch.setattr(config, "mark_gravity_pending", unexpected_gravity)
    domain_bulk_client.items = {"pihole1": [make_domain("ads.example")]}
    result = _domain_batch("delete", [("ads.example", "deny", "exact")])
    assert "gravity_pending" not in result
    assert result["results"][0]["changed"] is True


def test_domain_bulk_preserves_group_membership(domain_bulk_client):
    item = Domain(1, "ads.example", "deny", "exact", "Test", False, (0, 3), None, None)
    domain_bulk_client.items = {"pihole1": [item]}
    _domain_batch("enable", [(item.domain, item.type, item.kind)])
    assert domain_bulk_client.updated == [("pihole1", item, True, [0, 3])]


def test_domain_bulk_continues_after_one_failure(domain_bulk_client):
    items = [make_domain(f"{n}.example") for n in range(3)]
    domain_bulk_client.items = {"pihole1": items}
    domain_bulk_client.fail_updates = {items[1].domain}
    result = _domain_batch("disable", [(item.domain, item.type, item.kind) for item in items])
    assert len(domain_bulk_client.updated) == 3
    assert [item["success"] for item in result["results"]] == [True, False, True]
    assert result["results"][1]["error"] == "update failed"
    assert result["results"][1]["changed"] is False


def test_domain_bulk_rejects_unknown_action():
    with pytest.raises(ValidationError):
        domains.DomainBulkInput(action="purge", domains=[domains.DomainRef(domain="ads.example", type="deny", kind="exact")])


def test_domain_bulk_dedupes_case_and_trailing_dot_aliases(domain_bulk_client):
    domain_bulk_client.items = {"pihole1": [make_domain("ads.example.com")]}
    result = _domain_batch("disable", [(name, "deny", "exact") for name in ["Ads.Example.COM", "ads.example.com.", "ads.example.com"]])
    assert len(domain_bulk_client.updated) == 1
    assert result["results"] == [
        {"id": "deny:exact:Ads.Example.COM", "success": True, "changed": True, "error": None},
        {"id": "deny:exact:ads.example.com.", "success": False, "changed": False, "error": "Same domain as Ads.Example.COM"},
        {"id": "deny:exact:ads.example.com", "success": False, "changed": False, "error": "Same domain as Ads.Example.COM"},
    ]


def test_domain_bulk_does_not_dedupe_across_type_or_kind(domain_bulk_client):
    items = [make_domain("ads.example", type, kind) for type, kind in [("deny", "exact"), ("allow", "exact"), ("deny", "regex")]]
    domain_bulk_client.items = {"pihole1": items}
    result = _domain_batch("disable", [(item.domain, item.type, item.kind) for item in items])
    assert [entry[1] for entry in domain_bulk_client.updated] == items
    assert len(result["results"]) == 3


def test_domain_bulk_regex_patterns_are_not_normalized(domain_bulk_client):
    items = [make_domain(name, kind="regex") for name in ["ads", "ads.", "Ads"]]
    domain_bulk_client.items = {"pihole1": items}
    result = _domain_batch("disable", [("ads", "deny", "regex"), ("Ads", "deny", "regex"), ("ADS", "deny", "regex")])
    assert [entry[1].domain for entry in domain_bulk_client.updated] == ["ads", "Ads"]
    assert [item["success"] for item in result["results"]] == [True, True, False]
    assert result["results"][2]["error"] == "No longer present on this instance"


def test_domain_bulk_prefers_exact_match_on_duplicate_keys(domain_bulk_client):
    first = make_domain("ADS.example.", comment="first")
    second = make_domain("ads.example", comment="last")
    domain_bulk_client.items = {"pihole1": [first, second]}
    _domain_batch("disable", [(second.domain, second.type, second.kind)])
    assert domain_bulk_client.updated == [("pihole1", second, False, [0])]


def test_domain_bulk_reports_success_when_audit_fails(domain_bulk_client, monkeypatch, caplog):
    domain_bulk_client.items = {"pihole1": [make_domain("ads.example.com")]}

    def failing_record(*_args):
        raise OSError("audit write failed")

    monkeypatch.setattr(domains, "record", failing_record)
    with caplog.at_level("ERROR", logger="app.routes.domains"):
        result = _domain_batch("delete", [("ads.example.com", "deny", "exact")])
    assert result["results"] == [{"id": "deny:exact:ads.example.com", "success": True, "changed": True, "error": None}]
    assert domain_bulk_client.deleted == [("pihole1", "ads.example.com", "deny", "exact")]
    entries = [entry for entry in caplog.records if entry.name == "app.routes.domains"]
    assert len(entries) == 1
    assert "domain_delete" in entries[0].getMessage()
    assert "pihole1" in entries[0].getMessage()


def test_domain_delete_matches_regex_verbatim(monkeypatch):
    setup_domains(monkeypatch)
    first = make_domain("ads", kind="regex")
    second = make_domain("ads.", kind="regex")
    FakeDomainsPiholeClient.items = {"pihole1": [first, second]}

    domains.domain_delete(
        "pihole1", domains.DomainDeleteInput(domain="ads.", type="deny", kind="regex")
    )

    assert FakeDomainsPiholeClient.items["pihole1"] == [first]


def test_domain_update_regex_has_no_case_fallback(monkeypatch):
    setup_domains(monkeypatch)
    stored = make_domain("Ads", kind="regex")
    FakeDomainsPiholeClient.items = {"pihole1": [stored]}

    with pytest.raises(HTTPException) as caught:
        domains.domain_update(
            "pihole1",
            domains.DomainUpdateInput(domain="ads", type="deny", kind="regex", enabled=False),
        )

    assert caught.value.status_code == 404
    assert FakeDomainsPiholeClient.updated == []
    assert FakeDomainsPiholeClient.items["pihole1"] == [stored]


def test_domain_exact_match_beats_a_case_sibling(monkeypatch):
    setup_domains(monkeypatch)
    items = [make_domain("Ads.example"), make_domain("ads.example")]
    FakeDomainsPiholeClient.items = {"pihole1": items.copy()}

    for item in items:
        domains.domain_update(
            "pihole1",
            domains.DomainUpdateInput(domain=item.domain, type="deny", kind="exact", enabled=False),
        )

    assert FakeDomainsPiholeClient.updated == [("pihole1", item, False, [0]) for item in items]


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_domain_rejects_an_ambiguous_exact_match(monkeypatch, operation):
    setup_domains(monkeypatch)
    items = [make_domain("Ads.example"), make_domain("ads.example.")]
    FakeDomainsPiholeClient.items = {"pihole1": items.copy()}
    audits = []
    deletes = []
    monkeypatch.setattr(domains, "record", lambda *args: audits.append(args))
    monkeypatch.setattr(FakeDomainsPiholeClient, "delete_domain", lambda *args: deletes.append(args))

    with pytest.raises(HTTPException) as caught:
        if operation == "update":
            domains.domain_update(
                "pihole1",
                domains.DomainUpdateInput(domain="ADS.EXAMPLE", type="deny", kind="exact", enabled=False),
            )
        else:
            domains.domain_delete(
                "pihole1",
                domains.DomainDeleteInput(domain="ADS.EXAMPLE", type="deny", kind="exact"),
            )

    assert caught.value.status_code == 409
    assert caught.value.detail == "Domain matches more than one record on this instance; use the exact stored value"
    assert FakeDomainsPiholeClient.updated == []
    assert deletes == []
    assert FakeDomainsPiholeClient.items["pihole1"] == items
    assert audits == []


def test_domain_bulk_reports_an_ambiguous_exact_match(domain_bulk_client, monkeypatch):
    items = [make_domain("Ads.example"), make_domain("ads.example.")]
    domain_bulk_client.items = {"pihole1": items.copy()}
    audits = []
    monkeypatch.setattr(domains, "record", lambda *args: audits.append(args))

    result = _domain_batch("disable", [("ADS.EXAMPLE", "deny", "exact")])

    assert result["results"] == [{
        "id": "deny:exact:ADS.EXAMPLE", "success": False, "changed": False,
        "error": "Matches more than one record on this instance; use the exact stored value",
    }]
    assert domain_bulk_client.updated == []
    assert domain_bulk_client.deleted == []
    assert domain_bulk_client.items["pihole1"] == items
    assert audits == []


def test_domain_bulk_collapses_literal_duplicates_silently(domain_bulk_client):
    item = make_domain("ads.example")
    domain_bulk_client.items = {"pihole1": [item]}

    result = _domain_batch("disable", [("ads.example", "deny", "exact")] * 2)

    assert domain_bulk_client.updated == [("pihole1", item, False, [0])]
    assert result["results"] == [{
        "id": "deny:exact:ads.example", "success": True, "changed": True, "error": None,
    }]


def test_domains_list_reports_unreachable_instance(monkeypatch):
    setup_domains(monkeypatch)
    FakeDomainsPiholeClient.items = {"pihole1": [make_domain('ads.example')]}
    FakeDomainsPiholeClient.unreachable = {"pihole2"}
    result = domains.domains_list()
    assert result["errors"] == {"pihole2": "Unable to reach Pi-hole instance"}
    assert result["domains"][0]["domain"] == 'ads.example'
    assert "pihole1" in result["domains"][0]["instances"]


def test_domain_add_hides_transport_error_and_continues(monkeypatch):
    setup_domains(monkeypatch)
    FakeDomainsPiholeClient.transport_fail_add = {"pihole1"}
    result = domains.domain_add(domains.DomainAddInput(
        instances=["pihole1", "pihole2"], domain="ads.example", type="deny", kind="exact",
    ))
    assert [entry["success"] for entry in result["results"]] == [False, True]
    assert result["results"][0]["error"] == "Unable to update Pi-hole domains"
    assert [entry[0] for entry in FakeDomainsPiholeClient.added] == ["pihole2"]
    assert "boom" not in repr(result)


@pytest.mark.parametrize(("error", "status", "detail"), [
    (PiholeAPIError("write failed"), 502, "write failed"),
    (httpx.ConnectError("boom"), 503, "Unable to reach Pi-hole instance"),
])
@pytest.mark.parametrize("operation", ['domain_update', 'domain_delete'])
def test_domain_write_failure_is_not_audited(monkeypatch, operation, error, status, detail):
    setup_domains(monkeypatch)
    FakeDomainsPiholeClient.items = {"pihole1": [make_domain('ads.example')]}
    FakeDomainsPiholeClient.fail_write = {"pihole1": error}
    payloads = {
        "domain_update": domains.DomainUpdateInput(domain="ads.example", type="deny", kind="exact", enabled=False),
        "domain_delete": domains.DomainDeleteInput(domain="ads.example", type="deny", kind="exact"),
    }
    with pytest.raises(HTTPException) as caught:
        getattr(domains, operation)("pihole1", payloads[operation])
    assert caught.value.status_code == status
    assert caught.value.detail == detail
    assert FakeDomainsPiholeClient.audits == []


def _fail_audit(*_args):
    raise OSError("audit unavailable")


@pytest.mark.parametrize("operation", ["add", "update", "delete"])
def test_domain_single_routes_report_success_when_audit_fails(monkeypatch, caplog, operation):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    setup_domains(monkeypatch)
    fake = FakeDomainsPiholeClient
    fake.items = {"pihole1": [make_domain("ads.example")]}
    monkeypatch.setattr(domains, "record", _fail_audit)
    application = FastAPI()
    application.include_router(domains.router)
    payload = {"domain": "ads.example", "type": "deny", "kind": "exact"}
    with TestClient(application) as client:
        if operation == "add":
            response = client.post("/api/domains", json={**payload, "instances": ["pihole1", "pihole2"], "domain": "new.example"})
            assert [item["success"] for item in response.json()["results"]] == [True, True]
            assert [item[0] for item in fake.added] == ["pihole1", "pihole2"]
        elif operation == "update":
            response = client.patch("/api/domains/pihole1", json={**payload, "enabled": False})
            assert fake.items["pihole1"][0].enabled is False
        else:
            response = client.request("DELETE", "/api/domains/pihole1", json=payload)
            assert fake.items["pihole1"] == []
    assert 200 <= response.status_code < 300
    assert any(item.name == domains.__name__ and item.exc_info and "audit write failed" in item.message for item in caplog.records)
