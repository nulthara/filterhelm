from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Instance
from app.pihole_client import Adlist, Domain, Group, PiholeAPIError
from app.routes import groups


class FakeCredentials:
    def get(self, _name): return "secret"


class FakeGroupsPiholeClient:
    unreachable: set[str] = set()
    transport_fail_add: set[str] = set()
    fail_write: dict[str, Exception] = {}
    audits = []
    items = {}; added = []; updated = []; fail_add = set()
    adlists = {}; domains = {}; calls = []; pending = []; audits = []
    fail_adlist_delete = set(); fail_domain_update = set()
    def __init__(self, base_url, *_args, **_kwargs): self.name = base_url.removeprefix("http://")
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def get_groups(self):
        if self.name in self.unreachable:
            raise httpx.ConnectError("boom")
        return self.items.get(self.name, []).copy()
    def add_group(self, name, comment=""):
        if self.name in self.transport_fail_add:
            raise httpx.ConnectError("boom")
        if self.name in self.fail_add: raise PiholeAPIError("group add failed")
        self.added.append((self.name, name, comment)); self.items.setdefault(self.name, []).append(make_group(name, comment=comment))
    def update_group(self, item, *, enabled):
        if self.name in self.fail_write:
            raise self.fail_write[self.name]
        self.updated.append((self.name, item, enabled)); self.items[self.name] = [Group(current.id, current.name, current.comment, enabled, current.date_added, current.date_modified) if current == item else current for current in self.items.get(self.name, [])]
    def delete_group(self, name):
        self.calls.append((self.name, "delete_group", name))
        self.items[self.name] = [item for item in self.items.get(self.name, []) if item.name != name]
    def get_adlists(self):
        self.calls.append((self.name, "get_adlists"))
        return self.adlists.get(self.name, []).copy()
    def get_domains(self):
        self.calls.append((self.name, "get_domains"))
        return self.domains.get(self.name, []).copy()
    def delete_adlist(self, address):
        self.calls.append((self.name, "delete_adlist", address))
        if (self.name, address) in self.fail_adlist_delete:
            raise PiholeAPIError("adlist delete failed")
        self.adlists[self.name] = [item for item in self.adlists.get(self.name, []) if item.address != address]
    def delete_domain(self, domain, type, kind):
        self.calls.append((self.name, "delete_domain", domain, type, kind))
        self.domains[self.name] = [item for item in self.domains.get(self.name, []) if (item.domain, item.type, item.kind) != (domain, type, kind)]
    def update_adlist(self, item, *, enabled, groups):
        self.calls.append((self.name, "update_adlist", item.address, enabled, groups))
        self.adlists[self.name] = [replace(current, enabled=enabled, groups=tuple(groups)) if current == item else current for current in self.adlists.get(self.name, [])]
    def update_domain(self, item, *, enabled, groups):
        self.calls.append((self.name, "update_domain", item.domain, enabled, groups))
        if (self.name, item.domain) in self.fail_domain_update:
            raise PiholeAPIError("domain update failed")
        self.domains[self.name] = [replace(current, enabled=enabled, groups=tuple(groups)) if current == item else current for current in self.domains.get(self.name, [])]


def make_group(name, *, enabled=True, comment=""): return Group(5, name, comment, enabled, None, None)


def setup_groups(monkeypatch):
    FakeGroupsPiholeClient.unreachable = set()
    FakeGroupsPiholeClient.transport_fail_add = set()
    FakeGroupsPiholeClient.fail_write = {}
    FakeGroupsPiholeClient.audits = []
    instances = [Instance(name="pihole1", base_url="http://pihole1"), Instance(name="pihole2", base_url="http://pihole2")]
    monkeypatch.setattr(groups, "load_instances", lambda: instances.copy()); monkeypatch.setattr(groups, "credentials", FakeCredentials()); monkeypatch.setattr(groups, "PiholeClient", FakeGroupsPiholeClient); monkeypatch.setattr(groups, "record", lambda *args: FakeGroupsPiholeClient.audits.append(args))
    FakeGroupsPiholeClient.adlists = {}; FakeGroupsPiholeClient.domains = {}; FakeGroupsPiholeClient.calls = []
    FakeGroupsPiholeClient.pending = []; FakeGroupsPiholeClient.audits = []
    FakeGroupsPiholeClient.fail_adlist_delete = set(); FakeGroupsPiholeClient.fail_domain_update = set()
    monkeypatch.setattr(groups, "mark_gravity_pending", FakeGroupsPiholeClient.pending.append)
    monkeypatch.setattr(groups, "record", lambda *args: FakeGroupsPiholeClient.audits.append(args))
    FakeGroupsPiholeClient.items = {}; FakeGroupsPiholeClient.added = []; FakeGroupsPiholeClient.updated = []; FakeGroupsPiholeClient.fail_add = set()


def test_groups_are_returned_in_unified_view(monkeypatch):
    setup_groups(monkeypatch); FakeGroupsPiholeClient.items = {"pihole1": [make_group("same"), make_group("missing"), make_group("conflict")], "pihole2": [make_group("same"), make_group("conflict", enabled=False)]}
    result = groups.groups_list(); rows = {row["name"]: row for row in result["groups"]}
    assert result["instance_names"] == ["pihole1", "pihole2"]; assert rows["same"]["state"] == "consistent"; assert rows["missing"]["state"] == "missing"; assert rows["conflict"]["state"] == "conflict"


def test_group_add_reports_per_instance_results(monkeypatch):
    setup_groups(monkeypatch); FakeGroupsPiholeClient.fail_add = {"pihole2"}
    result = groups.group_add(groups.GroupAddInput(instances=["pihole1", "pihole2"], name="Kids", comment="New"))
    assert [item["success"] for item in result["results"]] == [True, False]; assert FakeGroupsPiholeClient.added == [("pihole1", "Kids", "New")]


def test_group_update_toggles_enabled_and_preserves_comment(monkeypatch):
    setup_groups(monkeypatch); original = make_group("Kids", comment="existing"); FakeGroupsPiholeClient.items = {"pihole1": [original]}
    groups.group_update("pihole1", groups.GroupUpdateInput(name="Kids", enabled=False))
    assert FakeGroupsPiholeClient.updated == [("pihole1", original, False)]; assert FakeGroupsPiholeClient.updated[0][1].name == "Kids"; assert FakeGroupsPiholeClient.updated[0][1].comment == "existing"


def test_group_delete_removes_by_name(monkeypatch):
    setup_groups(monkeypatch); FakeGroupsPiholeClient.items = {"pihole1": [make_group("Kids")]}; groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids")); assert FakeGroupsPiholeClient.items["pihole1"] == []


@pytest.mark.parametrize("name", ["", "x" * 101, "bad\x00name"])
def test_group_name_rejects_control_characters_and_invalid_lengths(name):
    with pytest.raises(ValidationError): groups.GroupAddInput(instances=["pihole1"], name=name)


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_group_not_found_on_instance_returns_404(monkeypatch, operation):
    setup_groups(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        if operation == "update": groups.group_update("pihole1", groups.GroupUpdateInput(name="missing", enabled=False))
        else: groups.group_delete("pihole1", groups.GroupDeleteInput(name="missing"))
    assert caught.value.status_code == 404


def setup_orphans(monkeypatch):
    setup_groups(monkeypatch)
    fake = FakeGroupsPiholeClient
    fake.items = {"pihole1": [replace(make_group("Kids"), id=3)]}
    fake.adlists = {"pihole1": [
        Adlist("https://lists.example/sole", "list comment", False, (3,), None, None, None),
        Adlist("https://lists.example/shared", "", True, (0, 3), None, None, None),
    ]}
    fake.domains = {"pihole1": [
        Domain(1, "ads.example", "deny", "exact", "domain comment", False, (3,), None, None),
        Domain(2, "other.example", "allow", "regex", "", True, (0,), None, None),
    ]}
    return fake


def test_group_orphans_lists_only_solely_scoped_entries(monkeypatch):
    fake = setup_orphans(monkeypatch)
    result = groups.group_orphans("pihole1", "Kids")
    assert result == {
        "instance": "pihole1", "name": "Kids", "group_id": 3,
        "adlists": [{"address": "https://lists.example/sole", "comment": "list comment", "enabled": False}],
        "domains": [{"domain": "ads.example", "type": "deny", "kind": "exact", "comment": "domain comment", "enabled": False}],
    }
    assert fake.calls == [("pihole1", "get_adlists"), ("pihole1", "get_domains")]
    assert fake.pending == fake.audits == []


def test_group_orphans_returns_empty_when_nothing_is_scoped(monkeypatch):
    fake = setup_orphans(monkeypatch)
    fake.adlists = {}; fake.domains = {}
    result = groups.group_orphans("pihole1", "Kids")
    assert result["adlists"] == result["domains"] == []


def test_group_orphans_unknown_group_returns_404(monkeypatch):
    setup_groups(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        groups.group_orphans("pihole1", "missing")
    assert caught.value.status_code == 404


@pytest.mark.parametrize("action", [None, "ignore"])
def test_group_delete_ignore_leaves_entries_untouched(monkeypatch, action):
    fake = setup_orphans(monkeypatch)
    payload = groups.GroupDeleteInput(name="Kids", **({"orphans": action} if action else {}))
    result = groups.group_delete("pihole1", payload)
    assert result == {"instance": "pihole1", "name": "Kids", "deleted": True, "orphans": [], "gravity_pending": False}
    assert fake.calls == [("pihole1", "delete_group", "Kids")]
    assert fake.items["pihole1"] == []
    assert len(fake.adlists["pihole1"]) == len(fake.domains["pihole1"]) == 2
    assert fake.audits[-1][3] == {}
    assert fake.pending == []


def test_group_delete_cascade_removes_orphans_before_the_group(monkeypatch):
    fake = setup_orphans(monkeypatch)
    result = groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids", orphans="delete"))
    assert fake.calls == [
        ("pihole1", "get_adlists"), ("pihole1", "get_domains"),
        ("pihole1", "delete_adlist", "https://lists.example/sole"),
        ("pihole1", "delete_domain", "ads.example", "deny", "exact"),
        ("pihole1", "delete_group", "Kids"),
    ]
    assert result["deleted"] is True
    assert result["orphans"] == [
        {"kind": "adlist", "id": "https://lists.example/sole", "success": True, "error": None},
        {"kind": "domain", "id": "ads.example", "success": True, "error": None},
    ]
    assert [item.address for item in fake.adlists["pihole1"]] == ["https://lists.example/shared"]
    assert [item.domain for item in fake.domains["pihole1"]] == ["other.example"]
    assert [item[1] for item in fake.audits] == ["adlist_delete", "domain_delete", "group_delete"]
    assert all(item[3] == {"cascade": "group_delete:Kids"} for item in fake.audits[:2])
    assert fake.audits[-1][3] == {"orphans": "delete", "orphan_count": 2}


def test_group_delete_reassign_moves_orphans_to_default(monkeypatch):
    fake = setup_orphans(monkeypatch)
    shared = fake.adlists["pihole1"][1]
    result = groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids", orphans="reassign"))
    assert fake.calls[2:] == [
        ("pihole1", "update_adlist", "https://lists.example/sole", False, [0]),
        ("pihole1", "update_domain", "ads.example", False, [0]),
        ("pihole1", "delete_group", "Kids"),
    ]
    assert result["deleted"] is True
    assert all(item["success"] for item in result["orphans"])
    assert fake.adlists["pihole1"][0].groups == fake.domains["pihole1"][0].groups == (0,)
    assert not fake.adlists["pihole1"][0].enabled and not fake.domains["pihole1"][0].enabled
    assert fake.adlists["pihole1"][0].comment == "list comment"
    assert fake.domains["pihole1"][0].comment == "domain comment"
    assert fake.adlists["pihole1"][1] == shared
    assert [item[1] for item in fake.audits] == ["adlist_update", "domain_update", "group_delete"]
    assert all(item[3] == {"cascade": "group_delete:Kids"} for item in fake.audits[:2])


@pytest.mark.parametrize("action", ["delete", "reassign"])
@pytest.mark.parametrize("has_adlist", [True, False])
def test_group_delete_marks_gravity_pending_only_when_an_adlist_changed(monkeypatch, action, has_adlist):
    fake = setup_orphans(monkeypatch)
    if not has_adlist: fake.adlists = {}
    result = groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids", orphans=action))
    assert result["gravity_pending"] is has_adlist
    assert fake.pending == (["pihole1"] if has_adlist else [])


@pytest.mark.parametrize("action", ["delete", "reassign"])
def test_group_delete_keeps_group_when_an_orphan_fails(monkeypatch, action):
    fake = setup_orphans(monkeypatch)
    fake.fail_adlist_delete = {("pihole1", "https://lists.example/sole")}
    fake.fail_domain_update = {("pihole1", "ads.example")}
    result = groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids", orphans=action))
    assert result["deleted"] is False
    assert fake.items["pihole1"][0].name == "Kids"
    assert [item["success"] for item in result["orphans"]] == ([False, True] if action == "delete" else [True, False])
    failed = next(item for item in result["orphans"] if not item["success"])
    assert failed["error"] == ("adlist delete failed" if action == "delete" else "domain update failed")
    assert not any(call[1] == "delete_group" for call in fake.calls)
    assert fake.pending == (["pihole1"] if action == "reassign" else [])


def test_group_delete_recomputes_orphans_at_write_time(monkeypatch):
    fake = setup_orphans(monkeypatch)
    old = fake.adlists["pihole1"][0]
    new = replace(old, address="https://lists.example/new")
    reads = iter([[old], [new]])
    monkeypatch.setattr(fake, "get_adlists", lambda self: next(reads))
    preview = groups.group_orphans("pihole1", "Kids")
    result = groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids", orphans="delete"))
    assert preview["adlists"][0]["address"] == old.address
    assert result["orphans"][0]["id"] == new.address
    assert ("pihole1", "delete_adlist", new.address) in fake.calls
    assert ("pihole1", "delete_adlist", old.address) not in fake.calls


@pytest.mark.parametrize("action", ["delete", "reassign"])
def test_group_delete_rejects_group_without_id_for_cascade_actions(monkeypatch, action):
    fake = setup_orphans(monkeypatch)
    fake.items["pihole1"] = [replace(make_group("Kids"), id=None)]
    with pytest.raises(HTTPException) as caught:
        groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids", orphans=action))
    assert caught.value.status_code == 409
    assert fake.calls == []
    assert groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids"))["deleted"] is True


def test_group_delete_input_rejects_unknown_orphan_action():
    with pytest.raises(ValidationError):
        groups.GroupDeleteInput(name="Kids", orphans="nuke")


def test_group_delete_rejects_default_group_without_confirmation(monkeypatch):
    setup_groups(monkeypatch)
    fake = FakeGroupsPiholeClient
    fake.items["pihole1"] = [replace(make_group("Default"), id=0)]
    with pytest.raises(HTTPException) as caught:
        groups.group_delete("pihole1", groups.GroupDeleteInput(name="Default"))
    assert caught.value.status_code == 400
    assert "reinstall" in caught.value.detail
    assert fake.calls == fake.pending == fake.audits == []


@pytest.mark.parametrize("action", ["ignore", "delete"])
def test_group_delete_allows_default_group_with_confirmation(monkeypatch, action):
    fake = setup_orphans(monkeypatch)
    fake.items["pihole1"] = [replace(make_group("Default"), id=0)]
    result = groups.group_delete("pihole1", groups.GroupDeleteInput(name="Default", orphans=action, confirm_default=True))
    assert result["deleted"] is True
    assert fake.items["pihole1"] == []
    assert fake.calls[-1] == ("pihole1", "delete_group", "Default")


@pytest.mark.parametrize("action", ["ignore", "delete", "reassign"])
def test_group_delete_default_check_ignores_orphans_choice(monkeypatch, action):
    fake = setup_orphans(monkeypatch)
    fake.items["pihole1"] = [replace(make_group("Renamed Default"), id=0)]
    with pytest.raises(HTTPException) as caught:
        groups.group_delete("pihole1", groups.GroupDeleteInput(name="Renamed Default", orphans=action))
    assert caught.value.status_code == 400
    assert "reinstall" in caught.value.detail
    assert fake.calls == []


def test_group_delete_confirm_default_is_a_no_op_for_non_default_groups(monkeypatch):
    fake = setup_orphans(monkeypatch)
    result = groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids", confirm_default=False))
    assert result["deleted"] is True
    assert fake.items["pihole1"] == []


def test_group_delete_rejects_reassign_for_default_group(monkeypatch):
    fake = setup_orphans(monkeypatch)
    fake.items["pihole1"] = [replace(make_group("Default"), id=0)]
    with pytest.raises(HTTPException) as caught:
        groups.group_delete("pihole1", groups.GroupDeleteInput(name="Default", orphans="reassign", confirm_default=True))
    assert caught.value.status_code == 400
    assert caught.value.detail == "Cannot reassign entries to the Default group while deleting it; choose 'delete' or 'ignore' instead."
    assert fake.calls == fake.pending == fake.audits == []


def test_group_orphans_rejects_group_without_id(monkeypatch):
    fake = setup_orphans(monkeypatch)
    fake.items["pihole1"] = [replace(make_group("Kids"), id=None)]
    with pytest.raises(HTTPException) as caught:
        groups.group_orphans("pihole1", "Kids")
    assert caught.value.status_code == 409
    assert fake.calls == []


def test_group_delete_continues_after_transport_failure_without_leaking_details(monkeypatch):
    fake = setup_orphans(monkeypatch)
    def fail(self, address):
        raise RuntimeError("transport detail containing secret")
    monkeypatch.setattr(fake, "delete_adlist", fail)
    result = groups.group_delete("pihole1", groups.GroupDeleteInput(name="Kids", orphans="delete"))
    assert result["deleted"] is False
    assert result["orphans"][0]["error"] == "Unable to update Pi-hole entry"
    assert result["orphans"][1]["success"] is True
    assert fake.pending == []


def test_groups_list_reports_unreachable_instance(monkeypatch):
    setup_groups(monkeypatch)
    FakeGroupsPiholeClient.items = {"pihole1": [make_group('Kids')]}
    FakeGroupsPiholeClient.unreachable = {"pihole2"}
    result = groups.groups_list()
    assert result["errors"] == {"pihole2": "Unable to reach Pi-hole instance"}
    assert result["groups"][0]["name"] == 'Kids'
    assert "pihole1" in result["groups"][0]["instances"]


def test_group_add_hides_transport_error_and_continues(monkeypatch):
    setup_groups(monkeypatch)
    FakeGroupsPiholeClient.transport_fail_add = {"pihole1"}
    result = groups.group_add(groups.GroupAddInput(
        instances=["pihole1", "pihole2"], name="Kids",
    ))
    assert [entry["success"] for entry in result["results"]] == [False, True]
    assert result["results"][0]["error"] == "Unable to update Pi-hole groups"
    assert [entry[0] for entry in FakeGroupsPiholeClient.added] == ["pihole2"]
    assert "boom" not in repr(result)


@pytest.mark.parametrize(("error", "status", "detail"), [
    (PiholeAPIError("write failed"), 502, "write failed"),
    (httpx.ConnectError("boom"), 503, "Unable to reach Pi-hole instance"),
])
@pytest.mark.parametrize("operation", ['group_update'])
def test_group_write_failure_is_not_audited(monkeypatch, operation, error, status, detail):
    setup_groups(monkeypatch)
    FakeGroupsPiholeClient.items = {"pihole1": [make_group('Kids')]}
    FakeGroupsPiholeClient.fail_write = {"pihole1": error}
    payloads = {
        "group_update": groups.GroupUpdateInput(name="Kids", enabled=False),
    }
    with pytest.raises(HTTPException) as caught:
        getattr(groups, operation)("pihole1", payloads[operation])
    assert caught.value.status_code == status
    assert caught.value.detail == detail
    assert FakeGroupsPiholeClient.audits == []
