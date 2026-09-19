from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Instance
from app.pihole_client import Client, PiholeAPIError
from app.routes import clients


class FakeCredentials:
    def get(self, _name): return "secret"


class FakeClientsPiholeClient:
    items = {}; added = []; updated = []; deleted = []; fail_add = set()
    def __init__(self, base_url, *_args, **_kwargs): self.name = base_url.removeprefix("http://")
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def get_clients(self): return self.items.get(self.name, []).copy()
    def add_client(self, identifier, comment=""):
        if self.name in self.fail_add: raise PiholeAPIError("client add failed")
        self.added.append((self.name, identifier, comment, [0])); self.items.setdefault(self.name, []).append(make_client(identifier, comment=comment))
    def update_client(self, item, *, groups): self.updated.append((self.name, item, groups)); self.items[self.name] = [Client(current.id, current.client, current.comment, tuple(groups), current.date_added, current.date_modified) if current == item else current for current in self.items.get(self.name, [])]
    def delete_client(self, identifier): self.deleted.append(identifier); self.items[self.name] = [item for item in self.items.get(self.name, []) if item.client != identifier]


def make_client(identifier, *, groups=(0,), comment=""): return Client(1, identifier, comment, groups, None, None)


def setup_clients(monkeypatch):
    instances = [Instance(name="pihole1", base_url="http://pihole1"), Instance(name="pihole2", base_url="http://pihole2")]
    monkeypatch.setattr(clients, "load_instances", lambda: instances.copy()); monkeypatch.setattr(clients, "credentials", FakeCredentials()); monkeypatch.setattr(clients, "PiholeClient", FakeClientsPiholeClient); monkeypatch.setattr(clients, "record", lambda *_args: None)
    FakeClientsPiholeClient.items = {}; FakeClientsPiholeClient.added = []; FakeClientsPiholeClient.updated = []; FakeClientsPiholeClient.deleted = []; FakeClientsPiholeClient.fail_add = set()


def test_clients_are_returned_in_unified_view(monkeypatch):
    setup_clients(monkeypatch); FakeClientsPiholeClient.items = {"pihole1": [make_client("both", groups=(0, 5)), make_client("missing")], "pihole2": [make_client("both", groups=(0, 9))]}
    result = clients.clients_list(); rows = {row["client"]: row for row in result["clients"]}
    assert rows["both"]["state"] == "consistent"; assert rows["missing"]["state"] == "missing"


def test_client_add_reports_per_instance_results(monkeypatch):
    setup_clients(monkeypatch); FakeClientsPiholeClient.fail_add = {"pihole2"}; result = clients.client_add(clients.ClientAddInput(instances=["pihole1", "pihole2"], client="host", comment="New"))
    assert [item["success"] for item in result["results"]] == [True, False]; assert FakeClientsPiholeClient.added == [("pihole1", "host", "New", [0])]


def test_client_update_groups_replaces_list_and_preserves_comment(monkeypatch):
    setup_clients(monkeypatch); original = make_client("host", comment="existing"); FakeClientsPiholeClient.items = {"pihole1": [original]}; clients.client_update_groups("pihole1", clients.ClientGroupsUpdateInput(client="host", groups=[0, 5])); assert FakeClientsPiholeClient.updated == [("pihole1", original, [0, 5])]; assert FakeClientsPiholeClient.updated[0][1].comment == "existing"


def test_client_delete_removes_by_identifier_and_uses_stored_case(monkeypatch):
    setup_clients(monkeypatch); FakeClientsPiholeClient.items = {"pihole1": [make_client("HOST")]}; clients.client_delete("pihole1", clients.ClientDeleteInput(client="host")); assert FakeClientsPiholeClient.items["pihole1"] == []; assert FakeClientsPiholeClient.deleted == ["HOST"]


@pytest.mark.parametrize("identifier", ["", "x" * 254, "bad\x00host"])
def test_client_identifier_rejects_control_characters_and_invalid_lengths(identifier):
    with pytest.raises(ValidationError): clients.ClientAddInput(instances=["pihole1"], client=identifier)


@pytest.mark.parametrize("identifier", ["192.168.2.0/24", "AA:BB:CC:DD:EE:FF", ":eth0"])
def test_client_identifier_accepts_supported_shapes(identifier): assert clients.ClientAddInput(instances=["pihole1"], client=identifier).client == identifier


@pytest.mark.parametrize("bad_groups", [[-1], [True]])
def test_client_groups_reject_invalid_entries(bad_groups):
    with pytest.raises(ValidationError): clients.ClientGroupsUpdateInput(client="host", groups=bad_groups)


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_client_not_found_on_instance_returns_404(monkeypatch, operation):
    setup_clients(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        if operation == "update": clients.client_update_groups("pihole1", clients.ClientGroupsUpdateInput(client="missing", groups=[]))
        else: clients.client_delete("pihole1", clients.ClientDeleteInput(client="missing"))
    assert caught.value.status_code == 404
