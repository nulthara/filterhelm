from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import datetime, timezone

import threading
from dataclasses import replace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import pihole_client
from app.config import Instance
from app.pihole_client import Adlist, BlockingState, PiholeAPIError, SystemInfo, FtlInfo, DatabaseInfo, SensorsInfo
from app.main import app, index
from app.routes import api
from app.url_policy import URLPolicyError


class FakeCredentials:
    def __init__(self):
        self.values = {"pihole1": "secret"}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class FakePiholeClient:
    actions: list[str] = []

    def run_action(self, action):
        self.actions.append(action)

    teleporter: bytes = b""

    def get_teleporter(self):
        return self.teleporter

    system_info = SystemInfo(1296249, 2097152, 54137, 2042507, 2.581453323364258,
                             8388604, 3017108, 35.9667472680794, 4, 6.974999904632568,
                             (0.6064453125, 1.32763671875, 1.234375))
    ftl_info = FtlInfo(432620297.203082, 0.02500000037252903, 1.4299392700195312)
    database_info = DatabaseInfo(460140544, 2636298, "3.53.1")
    sensors_info = SensorsInfo(57.0, 60.0, "C")
    info_calls: list[str] = []

    def get_system_info(self):
        self.info_calls.append("system")
        return self.system_info

    def get_ftl_info(self):
        self.info_calls.append("ftl")
        return self.ftl_info

    def get_database_info(self):
        self.info_calls.append("database")
        return self.database_info

    def get_sensors(self):
        self.info_calls.append("sensors")
        return self.sensors_info

    tree = {
        "dns": {
            "upstreams": ["1.1.1.1"],
            "hosts": ["192.168.1.10 server.lan"],
            "cnameRecords": ["alias.lan,server.lan"],
            "queryLogging": True,
            "revServers": [],
        },
        "misc": {"privacylevel": 0},
    }
    patches = []
    added_hosts = []
    deleted_hosts = []
    added_cnames = []
    deleted_cnames = []
    blocking = BlockingState(enabled=True, timer=None)
    blocking_changes = []
    adlists = []
    gravity_updates = 0
    versions = {
        "core": {"local": {"version": "v6.4.3"}, "remote": {"version": "v6.4.3"}},
        "web": {"local": {"version": "v6.6"}, "remote": {"version": "v6.6"}},
        "ftl": {"local": {"version": "v6.7"}, "remote": {"version": "v6.7"}},
        "docker": {"local": None, "remote": None},
    }

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def test_connection(self):
        pass

    def get_config(self):
        return self.tree

    def get_versions(self):
        return self.versions

    def patch_config(self, patch):
        self.patches.append(patch)
        return {}

    def add_dns_host(self, record):
        self.added_hosts.append(record)

    def delete_dns_host(self, record):
        self.deleted_hosts.append(record)

    def add_dns_cname(self, record):
        self.added_cnames.append(record)

    def delete_dns_cname(self, record):
        self.deleted_cnames.append(record)

    def get_blocking(self):
        return self.blocking

    def set_blocking(self, enabled, timer=None):
        self.blocking = BlockingState(enabled=enabled, timer=float(timer) if timer is not None else None)
        FakePiholeClient.blocking = self.blocking
        self.blocking_changes.append((enabled, timer))
        return self.blocking

    def get_adlists(self):
        return self.adlists.copy()

    def add_adlist(self, address, comment=""):
        self.adlists.append(Adlist(address, comment, True, (0,), None, None, None))
        FakePiholeClient.adlists = self.adlists

    def update_adlist(self, adlist, *, enabled, groups):
        self.adlists = [
            Adlist(item.address, item.comment, enabled, tuple(groups), item.date_updated, item.number, item.invalid_domains)
            if item.address == adlist.address else item
            for item in self.adlists
        ]
        FakePiholeClient.adlists = self.adlists

    def delete_adlist(self, address):
        self.adlists = [item for item in self.adlists if item.address != address]
        FakePiholeClient.adlists = self.adlists

    def update_gravity(self):
        FakePiholeClient.gravity_updates += 1


def setup_api(monkeypatch):
    instances = [Instance(name="pihole1", base_url="http://pihole1", notes="Primary")]
    credentials = FakeCredentials()
    pending = set()

    monkeypatch.setattr(api, "load_instances", lambda: instances.copy())
    monkeypatch.setattr(api, "save_instances", lambda new: instances.__setitem__(slice(None), new))
    monkeypatch.setattr(api, "credentials", credentials)
    monkeypatch.setattr(api, "PiholeClient", FakePiholeClient)
    monkeypatch.setattr(api, "record", lambda *_args: None)
    monkeypatch.setattr(api, "load_pending_gravity", lambda: pending.copy())
    monkeypatch.setattr(api, "mark_gravity_pending", pending.add)
    monkeypatch.setattr(api, "clear_gravity_pending", pending.discard)
    monkeypatch.setattr(api, "resolve_instance_destination", lambda value: value)
    monkeypatch.setattr(api, "screen_adlist_url", lambda value, **_kwargs: value)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pihole.toml", "# test config")
    FakePiholeClient.teleporter = buffer.getvalue()
    FakePiholeClient.actions = []
    FakePiholeClient.info_calls = []
    FakePiholeClient.patches = []
    FakePiholeClient.added_hosts = []
    FakePiholeClient.deleted_hosts = []
    FakePiholeClient.added_cnames = []
    FakePiholeClient.deleted_cnames = []
    FakePiholeClient.blocking = BlockingState(enabled=True, timer=None)
    FakePiholeClient.blocking_changes = []
    FakePiholeClient.adlists = [
        Adlist("https://lists.example/block.txt", "Primary", True, (0,), 1_700_000_000, 1234, 0)
    ]
    FakePiholeClient.gravity_updates = 0
    return instances, credentials


def test_instance_api_never_returns_credentials(monkeypatch):
    setup_api(monkeypatch)
    response = api.instances_list()

    assert response == {
        "instances": [
            {
                "name": "pihole1",
                "base_url": "http://pihole1",
                "notes": "Primary",
                "verify_ssl": True,
                "status": "success",
                "status_detail": "Reachable",
                "health": None,
                "versions": {
                    "core": {"local": "v6.4.3", "remote": "v6.4.3", "update_available": False},
                    "web": {"local": "v6.6", "remote": "v6.6", "update_available": False},
                    "ftl": {"local": "v6.7", "remote": "v6.7", "update_available": False},
                    "docker": {"local": None, "remote": None, "update_available": None},
                    "compatibility": "compatible",
                    "compatibility_detail": "Supported FTL 6.x API",
                    "update_available": False,
                },
            }
        ]
    }
    assert "secret" not in repr(response)
    assert FakePiholeClient.info_calls == []


def test_instance_list_fans_out_connection_checks(monkeypatch):
    instances = [Instance(name=f"pihole{number}", base_url=f"http://pihole{number}") for number in range(1, 4)]
    barrier = threading.Barrier(3)

    monkeypatch.setattr(api, "load_instances", lambda: instances)
    monkeypatch.setattr(api, "credentials", type("Credentials", (), {"get": lambda _self, _name: "secret"})())

    class ConcurrentClient(FakePiholeClient):
        def test_connection(self):
            barrier.wait(timeout=1)

    monkeypatch.setattr(api, "PiholeClient", ConcurrentClient)

    assert len(api.instances_list()["instances"]) == 3


def test_version_failure_does_not_hide_healthy_connection(monkeypatch):
    setup_api(monkeypatch)

    class VersionFailureClient(FakePiholeClient):
        def get_versions(self):
            raise PiholeAPIError("GET /api/info/version failed with HTTP 503")

    monkeypatch.setattr(api, "PiholeClient", VersionFailureClient)

    row = api.instances_list()["instances"][0]
    assert row["status"] == "success"
    assert row["versions"] is None
    assert row["status_detail"].endswith("version details unavailable")


def test_status_reports_blocking_state(monkeypatch):
    setup_api(monkeypatch)

    assert api.status_list() == {
        "instances": [
            {"name": "pihole1", "reachable": True, "enabled": True, "timer": None, "error": None}
        ]
    }


def test_timed_pause_and_resume_are_audited(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))

    paused = api.blocking_update(api.BlockingInput(instances=["pihole1"], enabled=False, duration=30))
    resumed = api.blocking_update(api.BlockingInput(instances=["pihole1"], enabled=True))

    assert paused["results"][0] == {
        "name": "pihole1", "success": True, "enabled": False, "timer": 30.0, "error": None
    }
    assert resumed["results"][0]["enabled"] is True
    assert FakePiholeClient.blocking_changes == [(False, 30), (True, None)]
    assert [entry[1] for entry in audits] == ["blocking_change", "blocking_change"]


def test_group_blocking_action_reports_partial_failures(monkeypatch):
    instances = [
        Instance(name="pihole1", base_url="http://pihole1"),
        Instance(name="pihole2", base_url="http://pihole2"),
    ]

    class PartialClient(FakePiholeClient):
        def __init__(self, base_url, *_args, **_kwargs):
            self.base_url = base_url

        def set_blocking(self, enabled, timer=None):
            if self.base_url == "http://pihole2":
                raise PiholeAPIError("POST /api/dns/blocking failed with HTTP 503")
            return BlockingState(enabled=enabled, timer=None)

    credentials = FakeCredentials()
    credentials.values["pihole2"] = "secret2"
    monkeypatch.setattr(api, "load_instances", lambda: instances)
    monkeypatch.setattr(api, "credentials", credentials)
    monkeypatch.setattr(api, "PiholeClient", PartialClient)
    monkeypatch.setattr(api, "record", lambda *_args: None)

    result = api.blocking_update(
        api.BlockingInput(instances=["pihole1", "pihole2"], enabled=False)
    )

    assert [item["success"] for item in result["results"]] == [True, False]
    assert result["results"][1]["error"] == "POST /api/dns/blocking failed with HTTP 503"


def test_adlists_are_returned_in_unified_view(monkeypatch):
    setup_api(monkeypatch)

    result = api.adlists_list()

    assert result["instance_names"] == ["pihole1"]
    assert result["adlists"][0]["address"] == "https://lists.example/block.txt"
    assert result["adlists"][0]["instances"]["pihole1"]["number"] == 1234
    assert result["adlists"][0]["state"] == "consistent"
    assert result["gravity_pending"] == []


def test_adlist_changes_mark_gravity_pending_until_explicit_update(monkeypatch):
    setup_api(monkeypatch)

    added = api.adlist_add(
        api.AdlistAddInput(
            instances=["pihole1"], address="https://lists.example/second.txt", comment="Second"
        )
    )
    assert added["results"][0]["success"] is True
    assert api.adlists_list()["gravity_pending"] == ["pihole1"]

    api.adlist_update(
        "pihole1",
        api.AdlistUpdateInput(address="https://lists.example/second.txt", enabled=False),
    )
    assert next(item for item in FakePiholeClient.adlists if item.address.endswith("second.txt")).enabled is False

    result = api.gravity_update("pihole1")
    assert result["gravity_pending"] is False
    assert FakePiholeClient.gravity_updates == 1
    assert api.adlists_list()["gravity_pending"] == []


def test_adlist_url_rejects_embedded_credentials():
    with pytest.raises(ValidationError):
        api.AdlistAddInput(
            instances=["pihole1"], address="https://user:secret@lists.example/block.txt"
        )


def test_adlist_match_ignores_a_normalized_trailing_slash(monkeypatch):
    setup_api(monkeypatch)
    FakePiholeClient.adlists = [Adlist("https://example.com/ads", "", True, (0,), None, None, None)]

    result = api.adlist_update(
        "pihole1", api.AdlistUpdateInput(address="https://example.com/ads/", enabled=False)
    )

    assert result["enabled"] is False


def test_adlist_add_accepts_a_public_block_list(monkeypatch):
    monkeypatch.delenv("FILTERHELM_PUBLIC_ALLOWLIST", raising=False)
    url = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/tif.txt"
    assert api.AdlistAddInput(instances=["pihole1"], address=url).address == url


def test_adlist_add_rejects_prohibited_literal_address():
    with pytest.raises(ValidationError):
        api.AdlistAddInput(instances=["pihole1"], address="http://169.254.169.254/x")


def test_adlist_update_delete_and_bulk_accept_unscreened_addresses():
    addresses = ["file:///etc/pihole/local.list", "https://user:pass@example.com/l#frag"]
    for address in addresses:
        assert api.AdlistUpdateInput(address=address, enabled=False).address == address
        assert api.AdlistDeleteInput(address=address).address == address
    assert api.AdlistBulkInput(action="delete", addresses=addresses).addresses == addresses
    with pytest.raises(ValidationError):
        api.AdlistBulkInput(action="delete", addresses=["x" * 2049])


def test_version_info_detects_updates_and_unsupported_ftl():
    result = api._version_info(
        {
            "core": {"local": {"version": "v5.18"}, "remote": {"version": "v6.4.3"}},
            "ftl": {"local": {"version": "v5.25"}, "remote": {"version": "v6.7"}},
        }
    )

    assert result["update_available"] is True
    assert result["core"]["update_available"] is True
    assert result["ftl"]["update_available"] is True
    assert result["compatibility"] == "incompatible"


def test_version_info_marks_future_ftl_major_as_untested():
    result = api._version_info(
        {"ftl": {"local": {"version": "v7.0"}, "remote": {"version": "v7.0"}}}
    )

    assert result["compatibility"] == "untested"


def test_settings_diff_is_read_only(monkeypatch):
    setup_api(monkeypatch)
    response = api.settings_diff("pihole1", api.SettingsInput(values={"dns.queryLogging": False}))

    assert response["changes"][0] == {
        "key": "dns.queryLogging",
        "label": "Query logging",
        "before": True,
        "after": False,
        "before_display": "True",
        "after_display": "False",
    }
    assert FakePiholeClient.patches == []


def test_settings_get_uses_pihole_revservers_array(monkeypatch):
    setup_api(monkeypatch)

    response = api.settings_get("pihole1")

    assert response["values"]["dns.revServers"] == []
    assert not any(key.startswith("dns.revServer.") for key in response["values"])


def test_settings_apply_writes_pihole_revservers_array(monkeypatch):
    setup_api(monkeypatch)
    reverse_server = "true,192.168.1.0/24,192.168.1.1,lan"

    response = api.settings_apply(
        "pihole1", api.SettingsApplyInput(values={"dns.revServers": [reverse_server]}, expected={"dns.revServers": []})
    )

    assert response["applied"] == 1
    assert FakePiholeClient.patches == [{"dns": {"revServers": [reverse_server]}}]


def test_settings_apply_disables_conditional_forwarding_with_empty_array(monkeypatch):
    setup_api(monkeypatch)
    FakePiholeClient.tree["dns"]["revServers"] = [
        "true,192.168.1.0/24,192.168.1.1,lan"
    ]
    try:
        response = api.settings_apply(
            "pihole1", api.SettingsApplyInput(values={"dns.revServers": []}, expected={"dns.revServers": FakePiholeClient.tree["dns"]["revServers"]})
        )
    finally:
        FakePiholeClient.tree["dns"]["revServers"] = []

    assert response["applied"] == 1
    assert FakePiholeClient.patches == [{"dns": {"revServers": []}}]


def test_dns_records_are_returned_in_unified_view(monkeypatch):
    setup_api(monkeypatch)

    result = api.dns_records_list()

    assert result["records"] == [
        {
            "hostname": "server.lan",
            "instances": {
                "pihole1": [
                    {"hostname": "server.lan", "address": "192.168.1.10", "raw": "192.168.1.10 server.lan"}
                ]
            },
            "state": "consistent",
        }
    ]


def test_dns_add_uses_item_level_operation(monkeypatch):
    setup_api(monkeypatch)

    api.dns_record_add(api.DNSRecordInput(instance="pihole1", hostname="new.lan", address="192.168.1.11"))

    assert FakePiholeClient.added_hosts == ["192.168.1.11 new.lan"]
    assert FakePiholeClient.patches == []


def test_dns_update_adds_before_removing_old_record(monkeypatch):
    setup_api(monkeypatch)

    api.dns_record_update(
        api.DNSRecordUpdate(
            instance="pihole1",
            old_hostname="server.lan",
            old_address="192.168.1.10",
            hostname="server.lan",
            address="192.168.1.12",
        )
    )

    assert FakePiholeClient.added_hosts == ["192.168.1.12 server.lan"]
    assert FakePiholeClient.deleted_hosts == ["192.168.1.10 server.lan"]


def test_dns_delete_matches_structurally(monkeypatch):
    setup_api(monkeypatch)
    original = FakePiholeClient.tree["dns"]["hosts"].copy()
    FakePiholeClient.tree["dns"]["hosts"] = ["192.168.1.1 Server.LAN"]
    try:
        api.dns_record_delete(
            api.DNSRecordInput(instance="pihole1", hostname="server.lan", address="192.168.1.1")
        )
    finally:
        FakePiholeClient.tree["dns"]["hosts"] = original

    assert FakePiholeClient.deleted_hosts == ["192.168.1.1 Server.LAN"]


def test_dns_delete_preserves_sibling_hostnames(monkeypatch):
    setup_api(monkeypatch)
    original = FakePiholeClient.tree["dns"]["hosts"].copy()
    FakePiholeClient.tree["dns"]["hosts"] = ["192.168.1.5 alpha beta"]

    class StatefulClient(FakePiholeClient):
        def delete_dns_host(self, record):
            super().delete_dns_host(record)
            self.tree["dns"]["hosts"].remove(record)

        def add_dns_host(self, record):
            super().add_dns_host(record)
            self.tree["dns"]["hosts"].append(record)

    monkeypatch.setattr(api, "PiholeClient", StatefulClient)
    try:
        api.dns_record_delete(
            api.DNSRecordInput(instance="pihole1", hostname="alpha", address="192.168.1.5")
        )
        listed = api.dns_records_list()
    finally:
        FakePiholeClient.tree["dns"]["hosts"] = original

    assert FakePiholeClient.deleted_hosts == ["192.168.1.5 alpha beta"]
    assert FakePiholeClient.added_hosts == ["192.168.1.5 beta"]
    assert [row["hostname"] for row in listed["records"]] == ["beta"]


def test_dns_delete_audits_when_trim_and_restore_both_fail(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    original = FakePiholeClient.tree["dns"]["hosts"].copy()
    old_line = "192.168.1.5 alpha beta"
    FakePiholeClient.tree["dns"]["hosts"] = [old_line]

    class TripleFailureClient(FakePiholeClient):
        def delete_dns_host(self, record):
            self.tree["dns"]["hosts"].remove(record)

        def add_dns_host(self, _record):
            raise PiholeAPIError("add failed")

    monkeypatch.setattr(api, "PiholeClient", TripleFailureClient)
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    try:
        with pytest.raises(HTTPException) as caught:
            api.dns_record_delete(
                api.DNSRecordInput(instance="pihole1", hostname="alpha", address="192.168.1.5")
            )
    finally:
        FakePiholeClient.tree["dns"]["hosts"] = original

    assert caught.value.status_code == 502
    assert old_line in caught.value.detail
    assert "re-add it in the Pi-hole Local DNS settings" in caught.value.detail
    assert audits == [("pihole1", "dns_record_delete_orphaned", {"dns.hosts": [old_line]}, {"dns.hosts": []})]


def test_dns_delete_restore_success_surfaces_original_error_without_orphan(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    original = FakePiholeClient.tree["dns"]["hosts"].copy()
    old_line = "192.168.1.5 alpha beta"
    replacement = "192.168.1.5 beta"
    FakePiholeClient.tree["dns"]["hosts"] = [old_line]

    class RecoveredClient(FakePiholeClient):
        def delete_dns_host(self, record):
            self.tree["dns"]["hosts"].remove(record)

        def add_dns_host(self, record):
            if record == replacement:
                raise PiholeAPIError("replacement add failed")
            self.tree["dns"]["hosts"].append(record)

    monkeypatch.setattr(api, "PiholeClient", RecoveredClient)
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    try:
        with pytest.raises(HTTPException) as caught:
            api.dns_record_delete(
                api.DNSRecordInput(instance="pihole1", hostname="alpha", address="192.168.1.5")
            )
        recovered = FakePiholeClient.tree["dns"]["hosts"].copy()
    finally:
        FakePiholeClient.tree["dns"]["hosts"] = original

    assert caught.value.status_code == 502
    assert caught.value.detail == "replacement add failed"
    assert recovered == [old_line]
    assert audits == []


def test_dns_update_audits_orphan_when_rollback_fails(monkeypatch):
    setup_api(monkeypatch)
    audits = []

    class DoubleFailureClient(FakePiholeClient):
        def delete_dns_host(self, _record):
            raise PiholeAPIError("delete failed")

    monkeypatch.setattr(api, "PiholeClient", DoubleFailureClient)
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))

    with pytest.raises(HTTPException) as caught:
        api.dns_record_update(
            api.DNSRecordUpdate(
                instance="pihole1",
                old_hostname="server.lan",
                old_address="192.168.1.10",
                hostname="server.lan",
                address="192.168.1.12",
            )
        )

    assert caught.value.status_code == 502
    assert "192.168.1.12 server.lan" in caught.value.detail
    assert audits[0][1] == "dns_record_update_orphaned"


def test_dns_update_audits_when_trim_and_restore_rollback_fails(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    original = FakePiholeClient.tree["dns"]["hosts"].copy()
    old_line = "192.168.1.5 alpha beta"
    new_record = "192.168.1.6 alpha"
    old_replacement = "192.168.1.5 beta"
    FakePiholeClient.tree["dns"]["hosts"] = [old_line]

    class TripleFailureClient(FakePiholeClient):
        def add_dns_host(self, record):
            if record in {old_replacement, old_line}:
                raise PiholeAPIError("restore failed")
            self.tree["dns"]["hosts"].append(record)

        def delete_dns_host(self, record):
            self.tree["dns"]["hosts"].remove(record)

    monkeypatch.setattr(api, "PiholeClient", TripleFailureClient)
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    try:
        with pytest.raises(HTTPException) as caught:
            api.dns_record_update(
                api.DNSRecordUpdate(
                    instance="pihole1",
                    old_hostname="alpha",
                    old_address="192.168.1.5",
                    hostname="alpha",
                    address="192.168.1.6",
                )
            )
    finally:
        FakePiholeClient.tree["dns"]["hosts"] = original

    assert caught.value.status_code == 502
    assert old_line in caught.value.detail
    assert audits == [
        (
            "pihole1",
            "dns_record_update_orphaned",
            {"dns.hosts": [old_line]},
            {"dns.hosts": [new_record]},
        )
    ]


def test_instance_update_preserves_password_when_left_blank(monkeypatch):
    instances, credentials = setup_api(monkeypatch)

    result = api.instance_update(
        "pihole1",
        api.InstanceUpdate(base_url="http://new-address/", notes="Updated"),
    )

    assert result == {
        "name": "pihole1",
        "base_url": "http://new-address",
        "notes": "Updated",
        "verify_ssl": True,
    }
    assert instances == [Instance(name="pihole1", base_url="http://new-address", notes="Updated")]
    assert credentials.get("pihole1") == "secret"


def test_instance_update_can_replace_password(monkeypatch):
    _, credentials = setup_api(monkeypatch)

    api.instance_update(
        "pihole1",
        api.InstanceUpdate(base_url="http://pihole1", app_password="replacement", notes="Primary"),
    )

    assert credentials.get("pihole1") == "replacement"


def test_instance_can_disable_tls_verification(monkeypatch):
    instances, _ = setup_api(monkeypatch)

    result = api.instance_update(
        "pihole1",
        api.InstanceUpdate(base_url="https://pihole1", notes="Local certificate", verify_ssl=False),
    )

    assert result["verify_ssl"] is False
    assert instances[0].verify_ssl is False


def test_instance_save_validates_resolved_destination(monkeypatch):
    instances, _ = setup_api(monkeypatch)

    def resolve(value):
        if "unsafe" in value:
            raise URLPolicyError("Instance resolves to prohibited address 169.254.169.254")
        return value

    monkeypatch.setattr(api, "resolve_instance_destination", resolve)
    payload = api.InstanceInput(
        name="pihole2", base_url="http://unsafe.lan", app_password="secret"
    )
    with pytest.raises(HTTPException) as caught:
        api.instance_save(payload)
    assert caught.value.status_code == 422
    assert instances == [Instance(name="pihole1", base_url="http://pihole1", notes="Primary")]

    result = api.instance_save(
        api.InstanceInput(name="pihole2", base_url="http://192.168.1.2", app_password="secret")
    )
    assert result["base_url"] == "http://192.168.1.2"
    route = next(route for route in api.router.routes if route.path == "/api/instances" and "POST" in route.methods)
    assert route.status_code == 201


def test_instance_update_validates_resolved_destination(monkeypatch):
    instances, _ = setup_api(monkeypatch)

    def resolve(value):
        if "unsafe" in value:
            raise URLPolicyError("Instance resolves to prohibited address 169.254.169.254")
        return value

    monkeypatch.setattr(api, "resolve_instance_destination", resolve)
    with pytest.raises(HTTPException) as caught:
        api.instance_update(
            "pihole1", api.InstanceUpdate(base_url="http://unsafe.lan", notes="Unsafe")
        )
    assert caught.value.status_code == 422
    assert instances[0].base_url == "http://pihole1"

    result = api.instance_update(
        "pihole1", api.InstanceUpdate(base_url="http://192.168.1.2", notes="Safe")
    )
    assert result["base_url"] == "http://192.168.1.2"
    route = next(
        route for route in api.router.routes
        if route.path == "/api/instances/{name}" and "PATCH" in route.methods
    )
    assert (route.status_code or 200) == 200


def test_instance_update_checks_existence_before_destination(monkeypatch):
    setup_api(monkeypatch)
    resolutions = []
    monkeypatch.setattr(api, "resolve_instance_destination", resolutions.append)

    with pytest.raises(HTTPException) as caught:
        api.instance_update(
            "missing", api.InstanceUpdate(base_url="http://unsafe.lan", notes="Missing")
        )

    assert caught.value.status_code == 404
    assert resolutions == []


def test_instance_input_rejects_unsafe_url():
    with pytest.raises(ValidationError):
        api.InstanceInput(name="bad", base_url="file:///etc/passwd", app_password="secret")


def test_instance_input_rejects_oversize_app_password():
    with pytest.raises(ValidationError):
        api.InstanceInput(name="bad", base_url="http://192.168.1.2", app_password="x" * 600)


def test_transport_error_response_is_sanitized():
    error = api._api_error(RuntimeError("socket details and internal hostname"))
    assert error.status_code == 503
    assert error.detail == "Unable to reach Pi-hole instance"


def test_settings_apply_writes_only_changed_managed_fields(monkeypatch):
    setup_api(monkeypatch)
    response = api.settings_apply(
        "pihole1", api.SettingsApplyInput(values={"dns.queryLogging": False, "misc.privacylevel": 0}, expected={"dns.queryLogging": True})
    )

    assert response["applied"] == 1
    assert FakePiholeClient.patches == [{"dns": {"queryLogging": False}}]


def test_settings_rejects_unknown_fields(monkeypatch):
    import pytest
    from fastapi import HTTPException

    setup_api(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        api.settings_apply("pihole1", api.SettingsApplyInput(values={"dangerous.unknown": True}, expected={}))

    assert caught.value.status_code == 422
    assert FakePiholeClient.patches == []


def test_settings_rejects_invalid_privacy_level(monkeypatch):
    import pytest
    from fastapi import HTTPException

    setup_api(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        api.settings_apply("pihole1", api.SettingsApplyInput(values={"misc.privacylevel": 4}, expected={}))

    assert caught.value.status_code == 422
    assert caught.value.detail == "Privacy level must be between 0 and 3"
    assert FakePiholeClient.patches == []


def test_root_opens_react_app():
    response = index()
    assert response.status_code == 307
    assert response.headers["location"] == "/app/"


def test_legacy_jinja_routes_are_removed():
    paths = set(app.openapi()["paths"])
    assert "/instances" not in paths
    assert "/settings" not in paths
    assert "/api/instances" in paths


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
def test_adlist_update_group_targeting(monkeypatch, extra, replacement_enabled, expected, before_enabled):
    setup_api(monkeypatch)
    enabled = before_enabled if replacement_enabled is None else replacement_enabled
    original = Adlist("https://lists.example/block.txt", "Existing", before_enabled, (0, 2), None, None, None)
    items = {"pihole1": [original], "pihole2": [original]}
    calls = []

    class TargetedClient(FakePiholeClient):
        def __init__(self, base_url, *_args, **_kwargs):
            self.name = base_url.removeprefix("http://")

        def get_adlists(self):
            return items[self.name].copy()

        def update_adlist(self, adlist, *, enabled, groups):
            calls.append((self.name, adlist, enabled, groups))
            items[self.name] = [Adlist(adlist.address, adlist.comment, enabled, tuple(groups), None, None, None)]

    monkeypatch.setattr(api, "PiholeClient", TargetedClient)
    payload = {"address": original.address, **extra}
    audit = []
    monkeypatch.setattr(api, "record", lambda *args: audit.append(args))
    result = api.adlist_update("pihole1", api.AdlistUpdateInput(**payload))
    assert result["groups"] == expected
    assert result["enabled"] is enabled
    assert calls == [("pihole1", original, enabled, expected)]
    assert items["pihole1"][0].groups == tuple(expected)
    assert items["pihole2"] == [original]
    assert audit[0][0:2] == ("pihole1", "adlist_update")
    assert audit[0][2]["groups"] == [0, 2]
    assert audit[0][3]["groups"] == expected
    assert audit[0][3]["enabled"] is enabled
    assert result["gravity_pending"] is True
    assert api.load_pending_gravity() == {"pihole1"}
    assert FakePiholeClient.gravity_updates == 0


@pytest.mark.parametrize("groups", [[True], ["5"], [5.0], [-1], list(range(101))])
def test_adlist_update_rejects_invalid_groups(groups):
    with pytest.raises(ValidationError):
        api.AdlistUpdateInput(address="https://lists.example/block.txt", enabled=True, groups=groups)


@pytest.mark.parametrize("extra", [{}, {"enabled": None}, {"groups": None}, {"enabled": None, "groups": None}])
def test_adlist_update_rejects_empty_payload(monkeypatch, extra):
    import asyncio

    import httpx
    from fastapi import FastAPI

    setup_api(monkeypatch)
    def unexpected_client(*_args):
        pytest.fail("An empty update must not access Pi-hole")
    monkeypatch.setattr(api, "_client", unexpected_client)
    test_app = FastAPI()
    test_app.include_router(api.router)

    async def patch():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url="http://test") as client:
            return await client.patch("/api/adlists/pihole1", json={**{"address": "https://lists.example/block.txt"}, **extra})

    response = asyncio.run(patch())
    assert response.status_code == 422
    assert "Provide enabled or groups" in response.text


def test_cname_records_are_returned_in_unified_view(monkeypatch):
    instances, _ = setup_api(monkeypatch)
    instances.append(Instance(name="pihole2", base_url="http://pihole2"))
    trees = {
        "pihole1": {"dns": {"cnameRecords": ["alias.lan,server.lan", "only.lan,server.lan", "different.lan,one.lan"]}},
        "pihole2": {"dns": {"cnameRecords": ["alias.lan,server.lan", "different.lan,two.lan"]}},
    }
    monkeypatch.setattr(api, "_cname_settings_tree", lambda instance: trees[instance.name])
    result = api.dns_cnames_list()
    assert result["instance_names"] == ["pihole1", "pihole2"]
    assert result["errors"] == {}
    rows = {row["hostname"]: row for row in result["records"]}
    assert {name: row["state"] for name, row in rows.items()} == {
        "alias.lan": "consistent", "only.lan": "missing", "different.lan": "conflict",
    }
    assert rows["alias.lan"]["instances"]["pihole1"] == [
        {"hostname": "alias.lan", "target": "server.lan", "ttl": None, "raw": "alias.lan,server.lan"},
    ]


def test_cname_list_skips_unparseable_entries(monkeypatch):
    setup_api(monkeypatch)
    monkeypatch.setattr(FakePiholeClient, "tree", {"dns": {"cnameRecords": [
        "garbage", "a,b,c,d", "alias.lan,server.lan,notanumber", "a,b,-1", "a,,3", "a,b,",
        " Alias.LAN., Server.LAN., 300 ",
    ]}})
    assert api.dns_cnames_list()["records"] == [{
        "hostname": "alias.lan", "state": "consistent", "instances": {"pihole1": [{
            "hostname": "alias.lan", "target": "server.lan", "ttl": 300,
            "raw": " Alias.LAN., Server.LAN., 300 ",
        }]},
    }]


def test_cname_add_uses_item_level_operation(monkeypatch):
    setup_api(monkeypatch)
    result = api.dns_cname_add(api.CNAMERecordInput(instance="pihole1", hostname="Alias2.LAN.", target="Server.LAN."))
    assert result == {"instance": "pihole1", "record": "alias2.lan,server.lan"}
    assert FakePiholeClient.added_cnames == ["alias2.lan,server.lan"]
    assert FakePiholeClient.patches == []


def test_cname_add_with_ttl_round_trips(monkeypatch):
    setup_api(monkeypatch)
    result = api.dns_cname_add(api.CNAMERecordInput(instance="pihole1", hostname="alias2.lan", target="server.lan", ttl=300))
    assert FakePiholeClient.added_cnames == ["alias2.lan,server.lan,300"]
    assert api._cname_entry(result["record"]) == ("alias2.lan", "server.lan", 300)


def test_cname_add_rejects_duplicate_alias(monkeypatch):
    setup_api(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.dns_cname_add(api.CNAMERecordInput(instance="pihole1", hostname="Alias.LAN.", target="other.lan"))
    assert exc.value.status_code == 409
    assert exc.value.detail == "That CNAME already exists on this instance"
    assert FakePiholeClient.added_cnames == []


@pytest.mark.parametrize("target,detail", [
    ("ALIAS.LAN.", "A CNAME cannot point at itself"),
    ("192.168.1.10", "A CNAME target must be a hostname, not an IP address"),
    ("2001:db8::1", "A CNAME target must be a hostname, not an IP address"),
])
def test_cname_add_rejects_self_reference_and_ip_target(monkeypatch, target, detail):
    setup_api(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.dns_cname_add(api.CNAMERecordInput(instance="pihole1", hostname="alias.lan", target=target))
    assert exc.value.status_code == 422
    assert exc.value.detail == detail
    assert FakePiholeClient.added_cnames == []


def test_cname_delete_matches_structurally(monkeypatch):
    setup_api(monkeypatch)
    monkeypatch.setattr(FakePiholeClient, "tree", {"dns": {"cnameRecords": ["Alias.LAN,Server.LAN"]}})
    result = api.dns_cname_delete(api.CNAMERecordInput(instance="pihole1", hostname="alias.lan", target="server.lan"))
    assert result == {"deleted": "Alias.LAN,Server.LAN"}
    assert FakePiholeClient.deleted_cnames == ["Alias.LAN,Server.LAN"]
    assert FakePiholeClient.patches == []


def test_cname_delete_missing_returns_404(monkeypatch):
    setup_api(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api.dns_cname_delete(api.CNAMERecordInput(instance="pihole1", hostname="missing.lan", target="server.lan"))
    assert exc.value.status_code == 404
    assert FakePiholeClient.deleted_cnames == []


def test_cname_update_removes_before_adding_new_record(monkeypatch):
    setup_api(monkeypatch)

    class OrderedClient(FakePiholeClient):
        def add_dns_cname(self, record):
            assert self.deleted_cnames == ["alias.lan,server.lan"]
            super().add_dns_cname(record)

    monkeypatch.setattr(api, "PiholeClient", OrderedClient)
    result = api.dns_cname_update(api.CNAMERecordUpdate(
        instance="pihole1", old_hostname="alias.lan", old_target="server.lan",
        hostname="alias.lan", target="new.lan", ttl=300,
    ))
    assert result == {"changed": True, "record": "alias.lan,new.lan,300"}
    assert FakePiholeClient.added_cnames == ["alias.lan,new.lan,300"]
    assert FakePiholeClient.deleted_cnames == ["alias.lan,server.lan"]
    assert FakePiholeClient.patches == []


def test_cname_update_rolls_back_old_record_when_add_fails(monkeypatch):
    setup_api(monkeypatch)

    class FailingClient(FakePiholeClient):
        def add_dns_cname(self, record):
            super().add_dns_cname(record)
            if record == "alias.lan,new.lan":
                raise PiholeAPIError("add failed")

    monkeypatch.setattr(api, "PiholeClient", FailingClient)
    with pytest.raises(HTTPException) as exc:
        api.dns_cname_update(api.CNAMERecordUpdate(
            instance="pihole1", old_hostname="alias.lan", old_target="server.lan",
            hostname="alias.lan", target="new.lan",
        ))
    assert exc.value.status_code == 502
    assert exc.value.detail == "add failed"
    assert FakePiholeClient.added_cnames == ["alias.lan,new.lan", "alias.lan,server.lan"]
    assert FakePiholeClient.deleted_cnames == ["alias.lan,server.lan"]


def test_cname_update_reports_partial_failure_when_rollback_fails(monkeypatch):
    setup_api(monkeypatch)

    class FailingClient(FakePiholeClient):
        def add_dns_cname(self, record):
            super().add_dns_cname(record)
            raise PiholeAPIError("add failed")

    monkeypatch.setattr(api, "PiholeClient", FailingClient)
    with pytest.raises(HTTPException) as exc:
        api.dns_cname_update(api.CNAMERecordUpdate(
            instance="pihole1", old_hostname="alias.lan", old_target="server.lan",
            hostname="alias.lan", target="new.lan",
        ))
    assert exc.value.status_code == 502
    assert "'alias.lan,server.lan' was removed and could not be restored" in exc.value.detail
    assert FakePiholeClient.deleted_cnames == ["alias.lan,server.lan"]


def test_cname_update_noop_returns_unchanged(monkeypatch):
    setup_api(monkeypatch)
    monkeypatch.setattr(api, "_client", lambda _: pytest.fail("No client calls for a no-op"))
    result = api.dns_cname_update(api.CNAMERecordUpdate(
        instance="pihole1", old_hostname="Alias.LAN.", old_target="Server.LAN.",
        hostname="alias.lan", target="server.lan",
    ))
    assert result == {"changed": False, "record": "alias.lan,server.lan"}


@pytest.mark.parametrize("cname", [False, True])
def test_partial_failure_audits_the_right_setting_key(monkeypatch, cname):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))

    class FailingClient(FakePiholeClient):
        def add_dns_cname(self, record):
            raise PiholeAPIError("delete failed")

        def delete_dns_host(self, record):
            raise PiholeAPIError("delete failed")

    monkeypatch.setattr(api, "PiholeClient", FailingClient)
    with pytest.raises(HTTPException) as exc:
        if cname:
            api.dns_cname_update(api.CNAMERecordUpdate(
                instance="pihole1", old_hostname="alias.lan", old_target="server.lan",
                hostname="alias.lan", target="new.lan",
            ))
        else:
            api.dns_record_update(api.DNSRecordUpdate(
                instance="pihole1", old_hostname="server.lan", old_address="192.168.1.10",
                hostname="server.lan", address="192.168.1.11",
            ))
    key = "dns.cnameRecords" if cname else "dns.hosts"
    event = "dns_cname_update_orphaned" if cname else "dns_record_update_orphaned"
    new_record = "alias.lan,new.lan" if cname else "192.168.1.11 server.lan"
    assert exc.value.status_code == 502
    before = ["alias.lan,server.lan"] if cname else []
    after = [] if cname else [new_record]
    assert audits == [("pihole1", event, {key: before}, {key: after})]


def test_cname_list_reports_unreachable_instance(monkeypatch):
    instances, _ = setup_api(monkeypatch)
    instances.append(Instance(name="pihole2", base_url="http://pihole2"))

    def settings_tree(instance):
        if instance.name == "pihole2":
            raise HTTPException(status_code=503, detail="Unable to reach Pi-hole instance")
        return {"dns": {"cnameRecords": ["alias.lan,server.lan"]}}

    monkeypatch.setattr(api, "_cname_settings_tree", settings_tree)
    result = api.dns_cnames_list()
    assert result["instance_names"] == ["pihole1", "pihole2"]
    assert result["errors"] == {"pihole2": "Unable to reach Pi-hole instance"}
    assert result["records"][0]["state"] == "consistent"


def test_cname_list_ttl_difference_is_conflict(monkeypatch):
    instances, _ = setup_api(monkeypatch)
    instances.append(Instance(name="pihole2", base_url="http://pihole2"))
    monkeypatch.setattr(api, "_cname_settings_tree", lambda instance: {"dns": {"cnameRecords": [
        "alias.lan,server.lan" + (",300" if instance.name == "pihole2" else ""),
    ]}})
    assert api.dns_cnames_list()["records"][0]["state"] == "conflict"


@pytest.mark.parametrize("ttl", [-1, 604801])
def test_cname_rejects_out_of_range_ttl(ttl):
    with pytest.raises(ValidationError):
        api.CNAMERecordInput(instance="pihole1", hostname="alias.lan", target="server.lan", ttl=ttl)
    with pytest.raises(HTTPException) as exc:
        api._cname_record("alias.lan", "server.lan", ttl)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("ttl", [None, 0, 604800])
def test_cname_accepts_ttl_boundaries(ttl):
    raw = api._cname_record("alias.lan", "server.lan", ttl)
    assert api._cname_entry(raw) == ("alias.lan", "server.lan", ttl)


@pytest.mark.parametrize("hostname,target", [("bad name", "server.lan"), ("alias.lan", "bad/name")])
def test_cname_rejects_invalid_names(hostname, target):
    with pytest.raises(HTTPException) as exc:
        api._cname_record(hostname, target, None)
    assert exc.value.status_code == 422
    assert exc.value.detail == "Enter a valid DNS hostname"


@pytest.mark.parametrize("stored", [[], ["alias.lan,other.lan"], ["alias.lan,server.lan,300"]])
def test_cname_update_rejects_stale_original(monkeypatch, stored):
    setup_api(monkeypatch)
    monkeypatch.setattr(FakePiholeClient, "tree", {"dns": {"cnameRecords": stored}})
    with pytest.raises(HTTPException) as exc:
        api.dns_cname_update(api.CNAMERecordUpdate(
            instance="pihole1", old_hostname="alias.lan", old_target="server.lan",
            hostname="alias.lan", target="new.lan",
        ))
    assert exc.value.status_code == 409
    assert exc.value.detail == "The original CNAME changed; refresh and try again"
    assert FakePiholeClient.added_cnames == FakePiholeClient.deleted_cnames == []


def test_cname_update_rejects_replacement_alias(monkeypatch):
    setup_api(monkeypatch)
    monkeypatch.setattr(FakePiholeClient, "tree", {"dns": {"cnameRecords": [
        "alias.lan,server.lan", "other.lan,another.lan",
    ]}})
    with pytest.raises(HTTPException) as exc:
        api.dns_cname_update(api.CNAMERecordUpdate(
            instance="pihole1", old_hostname="alias.lan", old_target="server.lan",
            hostname="Other.LAN.", target="new.lan",
        ))
    assert exc.value.status_code == 409
    assert exc.value.detail == "The replacement CNAME already exists"
    assert FakePiholeClient.added_cnames == FakePiholeClient.deleted_cnames == []


def test_cname_update_matches_stored_casing_and_changes_ttl(monkeypatch):
    setup_api(monkeypatch)
    monkeypatch.setattr(FakePiholeClient, "tree", {"dns": {"cnameRecords": ["Alias.LAN,Server.LAN,300"]}})
    result = api.dns_cname_update(api.CNAMERecordUpdate(
        instance="pihole1", old_hostname="alias.lan", old_target="server.lan", old_ttl=300,
        hostname="alias.lan", target="server.lan", ttl=0,
    ))
    assert result == {"changed": True, "record": "alias.lan,server.lan,0"}
    assert FakePiholeClient.added_cnames == ["alias.lan,server.lan,0"]
    assert FakePiholeClient.deleted_cnames == ["Alias.LAN,Server.LAN,300"]


@pytest.fixture
def cname_retry_setup(monkeypatch):
    instances, _ = setup_api(monkeypatch)
    sleeps = []
    monkeypatch.setattr(api.time, "sleep", sleeps.append)
    return instances[0], sleeps


class RetryingCnameClient(FakePiholeClient):
    def __init__(self, errors):
        self.errors = iter(errors)
        self.calls = 0

    def add_dns_cname(self, record):
        self.calls += 1
        error = next(self.errors)
        if error is not None:
            raise error
        super().add_dns_cname(record)

    def delete_dns_cname(self, record):
        self.calls += 1
        error = next(self.errors)
        if error is not None:
            raise error
        super().delete_dns_cname(record)


def test_cname_write_add_tolerates_duplicate_after_transport_retry(monkeypatch, cname_retry_setup):
    instance, sleeps = cname_retry_setup
    monkeypatch.setattr(FakePiholeClient, "tree", {"dns": {"cnameRecords": ["Alias.LAN., Server.LAN., 300"]}})
    client = RetryingCnameClient([httpx.RemoteProtocolError("restart"), PiholeAPIError("duplicate", status_code=400)])
    api._cname_write_add(instance, client, "alias.lan,server.lan,300")
    assert client.calls == 2
    assert sleeps == [1.5]


@pytest.mark.parametrize("stored", ["alias.lan,other.lan,300", "alias.lan,server.lan,301"])
def test_cname_write_add_does_not_trust_400_when_a_different_value_is_stored(monkeypatch, cname_retry_setup, stored):
    instance, _ = cname_retry_setup
    monkeypatch.setattr(FakePiholeClient, "tree", {"dns": {"cnameRecords": [stored]}})
    error = PiholeAPIError("alias taken", status_code=400)
    client = RetryingCnameClient([httpx.RemoteProtocolError("restart"), error])
    with pytest.raises(PiholeAPIError) as exc:
        api._cname_write_add(instance, client, "alias.lan,server.lan,300")
    assert exc.value is error
    assert client.calls == 2


def test_cname_write_add_propagates_when_verification_read_fails(monkeypatch, cname_retry_setup):
    instance, sleeps = cname_retry_setup
    reads = []

    def get_config(self):
        reads.append(True)
        raise httpx.ConnectError("restart")

    monkeypatch.setattr(FakePiholeClient, "get_config", get_config)
    client = RetryingCnameClient([httpx.RemoteProtocolError("restart"), PiholeAPIError("duplicate", status_code=400)])
    with pytest.raises(HTTPException) as exc:
        api._cname_write_add(instance, client, "alias.lan,server.lan")
    assert exc.value.status_code == 503
    assert len(reads) == 4
    assert sleeps == [1.5] * 4


def test_cname_write_add_first_attempt_duplicate_still_raises(cname_retry_setup):
    instance, sleeps = cname_retry_setup
    error = PiholeAPIError("duplicate", status_code=400)
    client = RetryingCnameClient([error])
    with pytest.raises(PiholeAPIError) as exc:
        api._cname_write_add(instance, client, "alias.lan,server.lan")
    assert exc.value is error
    assert client.calls == 1
    assert sleeps == []


@pytest.mark.parametrize("status_code", [401, 403, 500])
def test_cname_write_add_unrelated_error_after_transport_retry_still_raises(cname_retry_setup, status_code):
    instance, sleeps = cname_retry_setup
    error = PiholeAPIError("failure", status_code=status_code)
    client = RetryingCnameClient([httpx.RemoteProtocolError("restart"), error])
    with pytest.raises(PiholeAPIError) as exc:
        api._cname_write_add(instance, client, "alias.lan,server.lan")
    assert exc.value is error
    assert client.calls == 2
    assert sleeps == [1.5]


def test_cname_write_delete_tolerates_not_found_after_transport_retry(cname_retry_setup):
    instance, sleeps = cname_retry_setup
    client = RetryingCnameClient([httpx.RemoteProtocolError("restart"), PiholeAPIError("missing", status_code=404)])
    api._cname_write_delete(instance, client, "alias.lan,server.lan")
    assert client.calls == 2
    assert sleeps == [1.5]


def test_cname_write_delete_unrelated_error_after_transport_retry_still_raises(cname_retry_setup):
    instance, _ = cname_retry_setup
    error = PiholeAPIError("failure", status_code=500)
    client = RetryingCnameClient([httpx.RemoteProtocolError("restart"), error])
    with pytest.raises(PiholeAPIError) as exc:
        api._cname_write_delete(instance, client, "alias.lan,server.lan")
    assert exc.value is error
    assert client.calls == 2


@pytest.mark.parametrize("operation", ["add", "delete"])
def test_cname_write_exhausts_transport_retry_budget(cname_retry_setup, operation):
    instance, sleeps = cname_retry_setup
    error = httpx.ConnectError("restart")
    client = RetryingCnameClient([error] * 4)
    helper = api._cname_write_add if operation == "add" else api._cname_write_delete
    with pytest.raises(httpx.TransportError) as exc:
        helper(instance, client, "alias.lan,server.lan")
    assert exc.value is error
    assert client.calls == 4
    assert sleeps == [1.5] * 3


def test_cname_update_same_alias_changes_target_succeeds(monkeypatch):
    setup_api(monkeypatch)
    stored = ["a.lan,x.lan"]

    class UniqueAliasClient(FakePiholeClient):
        def get_config(self):
            return {"dns": {"cnameRecords": stored.copy()}}

        def add_dns_cname(self, record):
            if any(api._cname_entry(raw)[0] == api._cname_entry(record)[0] for raw in stored):
                raise PiholeAPIError("alias taken", status_code=400)
            stored.append(record)
            super().add_dns_cname(record)

        def delete_dns_cname(self, record):
            stored.remove(record)
            super().delete_dns_cname(record)

    monkeypatch.setattr(api, "PiholeClient", UniqueAliasClient)
    result = api.dns_cname_update(api.CNAMERecordUpdate(
        instance="pihole1", old_hostname="a.lan", old_target="x.lan",
        hostname="a.lan", target="y.lan",
    ))
    assert result == {"changed": True, "record": "a.lan,y.lan"}
    assert stored == ["a.lan,y.lan"]


def test_cname_update_survives_transport_blip_on_second_write(monkeypatch, cname_retry_setup):
    _, sleeps = cname_retry_setup
    audits = []
    calls = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))

    class BlipClient(FakePiholeClient):
        def add_dns_cname(self, record):
            calls.append(record)
            if len(calls) == 1:
                raise httpx.RemoteProtocolError("restart")
            super().add_dns_cname(record)

    monkeypatch.setattr(api, "PiholeClient", BlipClient)
    result = api.dns_cname_update(api.CNAMERecordUpdate(
        instance="pihole1", old_hostname="alias.lan", old_target="server.lan",
        hostname="alias.lan", target="new.lan",
    ))
    assert result == {"changed": True, "record": "alias.lan,new.lan"}
    assert calls == ["alias.lan,new.lan"] * 2
    assert FakePiholeClient.deleted_cnames == ["alias.lan,server.lan"]
    assert FakePiholeClient.added_cnames == ["alias.lan,new.lan"]
    assert sleeps == [1.5]
    assert [event[1] for event in audits] == ["dns_cname_update"]


def _assert_cname_read_recovers(monkeypatch, cname_retry_setup, operation):
    _, sleeps = cname_retry_setup
    calls = []

    def get_config(self):
        calls.append(True)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("restart")
        return self.tree

    monkeypatch.setattr(FakePiholeClient, "get_config", get_config)
    if operation == "list":
        result = api.dns_cnames_list()
        assert result["errors"] == {}
        assert result["records"][0]["hostname"] == "alias.lan"
    else:
        result = api.dns_cname_add(api.CNAMERecordInput(
            instance="pihole1", hostname="alias2.lan", target="server.lan",
        ))
        assert result["record"] == "alias2.lan,server.lan"
        assert FakePiholeClient.added_cnames == ["alias2.lan,server.lan"]
    assert len(calls) == 2
    assert sleeps == [1.5]


def test_cname_list_gives_up_after_repeated_transport_error(monkeypatch, cname_retry_setup):
    _, sleeps = cname_retry_setup
    calls = []

    def get_config(self):
        calls.append(True)
        raise httpx.TransportError("restart")

    monkeypatch.setattr(FakePiholeClient, "get_config", get_config)
    result = api.dns_cnames_list()
    assert result["errors"] == {"pihole1": "Unable to reach Pi-hole instance"}
    assert result["records"] == []
    assert len(calls) == 4
    assert sleeps == [1.5] * 3


def test_cname_settings_tree_does_not_retry_non_transport_errors(monkeypatch, cname_retry_setup):
    instance, sleeps = cname_retry_setup
    calls = []

    def get_config(self):
        calls.append(True)
        raise PiholeAPIError("config failed")

    monkeypatch.setattr(FakePiholeClient, "get_config", get_config)
    with pytest.raises(HTTPException) as exc:
        api._cname_settings_tree(instance)
    assert exc.value.status_code == 502
    assert exc.value.detail == "config failed"
    assert len(calls) == 1
    assert sleeps == []


def test_cname_list_retries_transport_error_and_recovers(monkeypatch, cname_retry_setup):
    _assert_cname_read_recovers(monkeypatch, cname_retry_setup, "list")


def test_cname_add_lookup_retries_transport_error(monkeypatch, cname_retry_setup):
    _assert_cname_read_recovers(monkeypatch, cname_retry_setup, "add")


@pytest.fixture
def bulk_client(monkeypatch):
    setup_api(monkeypatch)

    class BulkClient(FakePiholeClient):
        constructed = []
        reads = []
        updates = []
        deletes = []
        fail_updates = set()
        fail_additions = set()
        tree = {"dns": {"hosts": []}}

        def __init__(self, base_url, *_args, **_kwargs):
            self.constructed.append(base_url)

        def get_adlists(self):
            self.reads.append("adlists")
            return super().get_adlists()

        def get_config(self):
            self.reads.append("config")
            return self.tree

        def update_adlist(self, item, *, enabled, groups):
            self.updates.append((item, enabled, groups))
            if item.address in self.fail_updates:
                raise PiholeAPIError("update failed")
            super().update_adlist(item, enabled=enabled, groups=groups)

        def delete_adlist(self, address):
            self.deletes.append(address)
            if not any(item.address == address for item in self.adlists):
                raise PiholeAPIError("already deleted")
            super().delete_adlist(address)

        def delete_dns_host(self, value):
            self.deleted_hosts.append(value)
            self.tree["dns"]["hosts"].remove(value)

        def add_dns_host(self, value):
            self.added_hosts.append(value)
            if value in self.fail_additions:
                raise PiholeAPIError("add failed")
            self.tree["dns"]["hosts"].append(value)

    monkeypatch.setattr(api, "PiholeClient", BulkClient)
    return BulkClient


def _bulk_adlist(address: str, enabled: bool = True, groups: tuple[int, ...] = (0,)) -> Adlist:
    return Adlist(address, "Bulk test", enabled, groups, None, None, None)


def _adlist_batch(action: str, addresses: list[str]) -> dict:
    return api.adlist_bulk("pihole1", api.AdlistBulkInput(action=action, addresses=addresses))


def _dns_batch(records: list[tuple[str, str]]) -> dict:
    return api.dns_records_bulk(api.DNSRecordBulkInput(
        instance="pihole1", action="delete",
        records=[api.DNSRecordRef(address=address, hostname=hostname) for address, hostname in records],
    ))


def test_adlist_bulk_disable_writes_only_changed_records(bulk_client, monkeypatch):
    items = [_bulk_adlist(f"https://lists.example/{n}", enabled=n != 2) for n in range(3)]
    bulk_client.adlists = items
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    result = _adlist_batch("disable", [item.address for item in items])
    assert bulk_client.updates == [(items[0], False, [0]), (items[1], False, [0])]
    assert [item["changed"] for item in result["results"]] == [True, True, False]
    assert all(item["success"] for item in result["results"])
    assert len(audits) == 2
    assert all(entry[1] == "adlist_update" and entry[3]["bulk"] == "disable" for entry in audits)


def test_adlist_bulk_preserves_group_membership(bulk_client):
    item = _bulk_adlist("https://lists.example/groups", False, (0, 3))
    bulk_client.adlists = [item]
    _adlist_batch("enable", [item.address])
    assert bulk_client.updates == [(item, True, [0, 3])]


def test_adlist_bulk_delete_uses_stored_address(bulk_client):
    bulk_client.adlists = [_bulk_adlist("https://lists.example/block.txt/")]
    result = _adlist_batch("delete", ["https://lists.example/block.txt"])
    assert bulk_client.deletes == ["https://lists.example/block.txt/"]
    assert result["results"][0]["changed"] is True


def test_adlist_bulk_dedupes_canonical_aliases(bulk_client):
    result = _adlist_batch("disable", ["https://lists.example/block.txt", "https://lists.example/block.txt/", "https://LISTS.EXAMPLE/block.txt"])
    assert len(bulk_client.updates) == 1
    assert result["results"] == [
        {"id": "https://lists.example/block.txt", "success": True, "changed": True, "error": None},
        {"id": "https://lists.example/block.txt/", "success": False, "changed": False, "error": "Same block list as https://lists.example/block.txt"},
        {"id": "https://LISTS.EXAMPLE/block.txt", "success": False, "changed": False, "error": "Same block list as https://lists.example/block.txt"},
    ]


def test_adlist_bulk_delete_dedupes_canonical_aliases(bulk_client):
    result = _adlist_batch("delete", ["https://lists.example/block.txt", "https://lists.example/block.txt/"])
    assert bulk_client.deletes == ["https://lists.example/block.txt"]
    assert result["results"] == [
        {"id": "https://lists.example/block.txt", "success": True, "changed": True, "error": None},
        {"id": "https://lists.example/block.txt/", "success": False, "changed": False, "error": "Same block list as https://lists.example/block.txt"},
    ]


@pytest.mark.parametrize("enabled", [True, False])
def test_adlist_bulk_marks_gravity_pending_only_when_something_changed(bulk_client, monkeypatch, enabled):
    calls = []
    bulk_client.adlists = [_bulk_adlist(f"https://lists.example/{n}", enabled) for n in range(2)]
    def mark(name):
        calls.append(name)
    monkeypatch.setattr(api, "mark_gravity_pending", mark)
    monkeypatch.setattr(api, "load_pending_gravity", lambda: set(calls))
    result = _adlist_batch("disable", [item.address for item in bulk_client.adlists])
    assert calls == (["pihole1"] if enabled else [])
    assert result["gravity_pending"] is enabled
    assert bulk_client.gravity_updates == 0


def test_adlist_bulk_continues_after_one_failure(bulk_client):
    bulk_client.adlists = [_bulk_adlist(f"https://lists.example/{n}") for n in range(3)]
    bulk_client.fail_updates = {"https://lists.example/1"}
    result = _adlist_batch("disable", [item.address for item in bulk_client.adlists])
    assert len(bulk_client.updates) == 3
    assert [item["success"] for item in result["results"]] == [True, False, True]
    assert result["results"][1]["error"] == "update failed"
    assert result["results"][1]["changed"] is False


def test_adlist_bulk_reports_missing_records(bulk_client):
    result = _adlist_batch("delete", ["https://lists.example/missing"])
    assert result["results"] == [{"id": "https://lists.example/missing", "success": False, "changed": False, "error": "No longer present on this instance"}]
    assert bulk_client.deletes == []


def test_adlist_bulk_rejects_oversized_batch():
    with pytest.raises(ValidationError):
        api.AdlistBulkInput(action="delete", addresses=["https://lists.example/block.txt"] * 201)


def test_adlist_bulk_is_sequential_single_instance(bulk_client):
    bulk_client.adlists = [_bulk_adlist(f"https://lists.example/{n}") for n in range(3)]
    _adlist_batch("disable", [item.address for item in bulk_client.adlists])
    assert bulk_client.constructed == ["http://pihole1"]
    assert bulk_client.reads == ["adlists"]


def test_adlist_bulk_prefers_exact_match_on_duplicate_keys(bulk_client):
    first = _bulk_adlist("https://lists.example/block.txt/", groups=(0, 3))
    second = _bulk_adlist("https://lists.example/block.txt", groups=(4,))
    bulk_client.adlists = [first, second]
    _adlist_batch("disable", [second.address])
    assert bulk_client.updates == [(second, False, [4])]


def test_dns_bulk_delete_removes_each_record(bulk_client, monkeypatch):
    bulk_client.tree["dns"]["hosts"] = ["192.168.1.1 a.lan", "192.168.1.2 b.lan"]
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    result = _dns_batch([("192.168.1.1", "a.lan"), ("192.168.1.2", "b.lan")])
    assert bulk_client.deleted_hosts == ["192.168.1.1 a.lan", "192.168.1.2 b.lan"]
    assert bulk_client.tree["dns"]["hosts"] == []
    assert all(item["success"] and item["changed"] for item in result["results"])
    assert all(entry[1] == "dns_record_delete" and entry[3] == {"bulk": "delete"} for entry in audits)
    assert len(audits) == 2
    assert api.load_pending_gravity() == set()
    assert "gravity_pending" not in result


@pytest.mark.parametrize("hostnames", [("a.lan", "b.lan"), ("b.lan", "a.lan")])
def test_dns_bulk_delete_handles_two_hostnames_on_one_line(bulk_client, hostnames):
    bulk_client.tree["dns"]["hosts"] = ["192.168.1.1 a.lan b.lan"]
    result = _dns_batch([("192.168.1.1", hostname) for hostname in hostnames])
    assert bulk_client.deleted_hosts == ["192.168.1.1 a.lan b.lan", f"192.168.1.1 {hostnames[1]}"]
    assert bulk_client.added_hosts == [f"192.168.1.1 {hostnames[1]}"]
    assert bulk_client.tree["dns"]["hosts"] == []
    assert all(item["success"] for item in result["results"])


def test_dns_bulk_delete_aborts_on_rollback_failure(bulk_client, monkeypatch):
    raw = "192.168.1.1 a.lan b.lan"
    bulk_client.tree["dns"]["hosts"] = [raw, "192.168.1.2 later.lan"]
    bulk_client.fail_additions = {raw, "192.168.1.1 b.lan"}
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    with pytest.raises(HTTPException) as caught:
        _dns_batch([("192.168.1.1", "a.lan"), ("192.168.1.2", "later.lan")])
    assert caught.value.status_code == 502
    assert raw in caught.value.detail
    assert "Nothing else in this batch was applied" in caught.value.detail
    assert bulk_client.deleted_hosts == [raw]
    assert bulk_client.tree["dns"]["hosts"] == ["192.168.1.2 later.lan"]
    assert [entry[1] for entry in audits] == ["dns_record_delete_orphaned"]


def test_dns_bulk_delete_continues_after_successful_rollback(bulk_client):
    raw = "192.168.1.1 a.lan b.lan"
    bulk_client.tree["dns"]["hosts"] = [raw, "192.168.1.2 later.lan"]
    bulk_client.fail_additions = {"192.168.1.1 b.lan"}
    result = _dns_batch([("192.168.1.1", "a.lan"), ("192.168.1.2", "later.lan")])
    assert [item["success"] for item in result["results"]] == [False, True]
    assert result["results"][0]["error"] == "add failed"
    assert result["results"][0]["changed"] is False
    assert bulk_client.deleted_hosts == [raw, "192.168.1.2 later.lan"]
    assert bulk_client.tree["dns"]["hosts"] == [raw]


def test_dns_bulk_rollback_error_names_prior_deletions(bulk_client):
    raw = "192.168.1.3 c.lan d.lan"
    bulk_client.tree["dns"]["hosts"] = ["192.168.1.1 a.lan", "192.168.1.2 b.lan", raw]
    bulk_client.fail_additions = {raw, "192.168.1.3 d.lan"}
    with pytest.raises(HTTPException) as caught:
        _dns_batch([("192.168.1.1", "a.lan"), ("192.168.1.2", "b.lan"), ("192.168.1.3", "c.lan")])
    assert caught.value.status_code == 502
    for value in [raw, "192.168.1.1 a.lan", "192.168.1.2 b.lan"]:
        assert value in caught.value.detail
    assert "Already deleted in this batch" in caught.value.detail


@pytest.mark.parametrize("raw,address,hostname", [
    ("192.168.1.1 server.lan", "192.168.1.1", " Server.LAN. "),
    ("fd00::1 box.lan", "fd00:0:0:0:0:0:0:1", "BOX.LAN."),
])
def test_dns_bulk_normalizes_hostname_and_address(bulk_client, raw, address, hostname):
    bulk_client.tree["dns"]["hosts"] = [raw]
    result = _dns_batch([(address, hostname)])
    assert bulk_client.deleted_hosts == [raw]
    assert result["results"] == [{"id": f"{address} {hostname}", "success": True, "changed": True, "error": None}]


def test_dns_bulk_dedupes_same_record_spelled_twice(bulk_client):
    bulk_client.tree["dns"]["hosts"] = ["192.168.1.1 server.lan"]
    result = _dns_batch([("192.168.1.1", "server.lan"), ("192.168.1.1", "SERVER.LAN.")])
    assert bulk_client.deleted_hosts == ["192.168.1.1 server.lan"]
    assert len(result["results"]) == 1
    assert result["results"][0]["success"] is True


def test_dns_bulk_is_sequential_single_instance(bulk_client):
    bulk_client.tree["dns"]["hosts"] = ["192.168.1.1 a.lan", "192.168.1.2 b.lan"]
    _dns_batch([("192.168.1.1", "a.lan"), ("192.168.1.2", "b.lan")])
    assert bulk_client.constructed == ["http://pihole1"]
    assert bulk_client.reads == ["config"]


def test_dns_bulk_missing_or_malformed_record_does_not_abort(bulk_client):
    bulk_client.tree["dns"]["hosts"] = ["192.168.1.1 server.lan"]
    result = _dns_batch([("invalid", "bad host"), ("192.168.1.1", "server.lan")])
    assert [item["success"] for item in result["results"]] == [False, True]
    assert result["results"][0]["error"] == "Record no longer exists on this instance"


def test_cname_bulk_delete_removes_each_record(monkeypatch):
    setup_api(monkeypatch)
    FakePiholeClient.tree["dns"]["cnameRecords"] = ["a.lan,x.lan", "b.lan,y.lan"]
    result = api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete", records=[
            {"hostname": "a.lan", "target": "x.lan"},
            {"hostname": "b.lan", "target": "y.lan"},
        ],
    ))
    assert FakePiholeClient.deleted_cnames == ["a.lan,x.lan", "b.lan,y.lan"]
    assert result == {"instance": "pihole1", "action": "delete", "results": [
        {"id": raw, "success": True, "changed": True, "error": None}
        for raw in ["a.lan,x.lan", "b.lan,y.lan"]
    ]}
    assert api.load_pending_gravity() == set()


def test_cname_bulk_delete_uses_stored_raw_string(monkeypatch):
    setup_api(monkeypatch)
    FakePiholeClient.tree["dns"]["cnameRecords"] = ["Alias.LAN,Server.LAN"]
    result = api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete",
        records=[{"hostname": "alias.lan", "target": "server.lan"}],
    ))
    assert FakePiholeClient.deleted_cnames == ["Alias.LAN,Server.LAN"]
    assert result["results"] == [
        {"id": "alias.lan,server.lan", "success": True, "changed": True, "error": None},
    ]


def test_cname_bulk_delete_reports_missing_record(monkeypatch):
    setup_api(monkeypatch)
    result = api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete",
        records=[{"hostname": "missing.lan", "target": "server.lan"}],
    ))
    assert result["results"] == [{
        "id": "missing.lan,server.lan", "success": False, "changed": False,
        "error": "CNAME no longer exists on this instance",
    }]
    assert FakePiholeClient.deleted_cnames == []


def test_cname_bulk_delete_dedupes_repeated_ref(monkeypatch):
    setup_api(monkeypatch)
    FakePiholeClient.tree["dns"]["cnameRecords"] = ["alias.lan,server.lan"]
    result = api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete", records=[
            {"hostname": "Alias.LAN", "target": "server.lan"},
            {"hostname": "alias.lan.", "target": "server.lan."},
            {"hostname": "alias.lan", "target": "server.lan"},
        ],
    ))
    assert FakePiholeClient.deleted_cnames == ["alias.lan,server.lan"]
    assert len(result["results"]) == 1
    assert result["results"][0]["success"] is True


def test_cname_bulk_delete_continues_after_one_failure(monkeypatch):
    setup_api(monkeypatch)

    class FailingClient(FakePiholeClient):
        def delete_dns_cname(self, record):
            if record == "b.lan,y.lan":
                raise PiholeAPIError("boom", status_code=500)
            super().delete_dns_cname(record)

    monkeypatch.setattr(api, "PiholeClient", FailingClient)
    FakePiholeClient.tree["dns"]["cnameRecords"] = ["a.lan,x.lan", "b.lan,y.lan", "c.lan,z.lan"]
    result = api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete", records=[
            {"hostname": "a.lan", "target": "x.lan"},
            {"hostname": "b.lan", "target": "y.lan"},
            {"hostname": "c.lan", "target": "z.lan"},
        ],
    ))
    assert FakePiholeClient.deleted_cnames == ["a.lan,x.lan", "c.lan,z.lan"]
    assert [item["success"] for item in result["results"]] == [True, False, True]
    assert result["results"][1] == {
        "id": "b.lan,y.lan", "success": False, "changed": False, "error": "boom",
    }


def test_cname_bulk_delete_rides_out_transport_blip(monkeypatch, cname_retry_setup):
    _, sleeps = cname_retry_setup
    FakePiholeClient.tree["dns"]["cnameRecords"] = ["alias.lan,server.lan"]
    client = RetryingCnameClient([httpx.RemoteProtocolError("restart"), None])
    monkeypatch.setattr(api, "PiholeClient", lambda *_args, **_kwargs: client)
    result = api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete",
        records=[{"hostname": "alias.lan", "target": "server.lan"}],
    ))
    assert client.calls == 2
    assert sleeps == [1.5]
    assert client.deleted_cnames == ["alias.lan,server.lan"]
    assert result["results"] == [
        {"id": "alias.lan,server.lan", "success": True, "changed": True, "error": None},
    ]


def test_cname_bulk_delete_is_sequential_single_instance(bulk_client):
    bulk_client.tree["dns"]["cnameRecords"] = ["a.lan,x.lan", "b.lan,y.lan"]
    api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete", records=[
            {"hostname": "a.lan", "target": "x.lan"},
            {"hostname": "b.lan", "target": "y.lan"},
        ],
    ))
    assert bulk_client.constructed == ["http://pihole1"]
    assert bulk_client.reads == ["config"]
    assert bulk_client.deleted_cnames == ["a.lan,x.lan", "b.lan,y.lan"]


def test_cname_bulk_delete_audits_each_removed_record(monkeypatch):
    setup_api(monkeypatch)
    FakePiholeClient.tree["dns"]["cnameRecords"] = ["a.lan,x.lan", "b.lan,y.lan"]
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete", records=[
            {"hostname": "a.lan", "target": "x.lan"},
            {"hostname": "b.lan", "target": "y.lan"},
            {"hostname": "missing.lan", "target": "z.lan"},
        ],
    ))
    assert audits == [
        ("pihole1", "dns_cname_delete", {"dns.cnameRecords": raw}, {"bulk": "delete"})
        for raw in ["a.lan,x.lan", "b.lan,y.lan"]
    ]


def test_cname_bulk_delete_does_not_mislabel_when_audit_fails(monkeypatch):
    setup_api(monkeypatch)
    FakePiholeClient.tree["dns"]["cnameRecords"] = ["a.lan,x.lan", "b.lan,y.lan"]
    audits = []

    def failing_record(*args):
        audits.append(args)
        if len(audits) == 2:
            raise OSError("audit write failed")

    monkeypatch.setattr(api, "record", failing_record)
    result = api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete", records=[
            {"hostname": "a.lan", "target": "x.lan"},
            {"hostname": "b.lan", "target": "y.lan"},
        ],
    ))
    assert len(result["results"]) == 2
    assert all(item["success"] is True and item["changed"] is True for item in result["results"])
    assert len(audits) == 2
    assert FakePiholeClient.deleted_cnames == ["a.lan,x.lan", "b.lan,y.lan"]


def test_cname_bulk_rejects_oversized_batch():
    with pytest.raises(ValidationError):
        api.CNAMERecordBulkInput(
            instance="pihole1", action="delete",
            records=[{"hostname": f"alias{n}.lan", "target": "server.lan"} for n in range(201)],
        )


def test_cname_bulk_rejects_unknown_action():
    with pytest.raises(ValidationError):
        api.CNAMERecordBulkInput(
            instance="pihole1", action="disable",
            records=[{"hostname": "alias.lan", "target": "server.lan"}],
        )


def test_adlist_key_is_total_for_unparseable_addresses():
    assert api._adlist_key("http://[broken/list") == "http://[broken/list"
    assert api._adlist_key("http://lists.example:99999/l") != api._adlist_key("http://lists.example/l")


def test_adlist_bulk_handles_a_stored_address_that_will_not_parse(bulk_client):
    address = "http://lists.example:99999/l"
    bulk_client.adlists = [_bulk_adlist(address)]
    missing = _adlist_batch("delete", ["http://lists.example/l"])
    assert missing["results"] == [{"id": "http://lists.example/l", "success": False, "changed": False, "error": "No longer present on this instance"}]
    assert bulk_client.deletes == []
    result = _adlist_batch("delete", [address])
    assert result["results"] == [{"id": address, "success": True, "changed": True, "error": None}]
    assert bulk_client.deletes == [address]


def _fail_audit(*_args):
    raise OSError("audit write failed")


def test_adlist_bulk_reports_success_and_marks_gravity_when_audit_fails(bulk_client, monkeypatch):
    addresses = ["https://lists.example/one", "https://lists.example/two"]
    bulk_client.adlists = [_bulk_adlist(address) for address in addresses]
    monkeypatch.setattr(api, "record", _fail_audit)
    result = _adlist_batch("delete", addresses)
    assert len(result["results"]) == 2
    assert all(item["success"] is True and item["changed"] is True for item in result["results"])
    assert "pihole1" in api.load_pending_gravity()
    assert bulk_client.deletes == addresses
    assert FakePiholeClient.adlists == []


def test_dns_records_bulk_reports_success_when_audit_fails(bulk_client, monkeypatch):
    bulk_client.tree["dns"]["hosts"] = ["192.168.1.1 a.lan"]
    monkeypatch.setattr(api, "record", _fail_audit)
    result = _dns_batch([("192.168.1.1", "a.lan")])
    assert len(result["results"]) == 1
    assert result["results"][0]["success"] is True
    assert result["results"][0]["changed"] is True
    assert bulk_client.deleted_hosts == ["192.168.1.1 a.lan"]
    assert bulk_client.tree["dns"]["hosts"] == []


def test_dns_cnames_bulk_reports_success_when_audit_fails(monkeypatch):
    setup_api(monkeypatch)
    FakePiholeClient.tree["dns"]["cnameRecords"] = ["alias.lan,server.lan"]
    monkeypatch.setattr(api, "record", _fail_audit)
    result = api.dns_cnames_bulk(api.CNAMERecordBulkInput(
        instance="pihole1", action="delete",
        records=[{"hostname": "alias.lan", "target": "server.lan"}],
    ))
    assert result["results"] == [{"id": "alias.lan,server.lan", "success": True, "changed": True, "error": None}]
    assert FakePiholeClient.deleted_cnames == ["alias.lan,server.lan"]


@pytest.mark.parametrize("module_name,action", [
    ("api", "adlist_delete"), ("api", "dns_record_delete"),
    ("api", "dns_cname_delete"), ("domains", "domain_delete"),
])
def test_bulk_audit_failure_is_logged(monkeypatch, caplog, module_name, action):
    from app.routes import domains

    module = api if module_name == "api" else domains
    monkeypatch.setattr(module, "record", _fail_audit)
    with caplog.at_level("ERROR", logger=module.__name__):
        module._audit_landed_write("pihole1", action, {}, {"bulk": "delete"})
    entries = [entry for entry in caplog.records if entry.name == module.__name__]
    assert len(entries) == 1
    assert entries[0].levelname == "ERROR"
    assert action in entries[0].getMessage()
    assert "pihole1" in entries[0].getMessage()


@pytest.mark.parametrize("module_name", ["api", "domains"])
def test_audit_landed_write_swallows_and_delegates(monkeypatch, module_name):
    from app.routes import domains

    module = api if module_name == "api" else domains
    audits = []
    args = ("pihole1", "example_delete", {"before": True}, {"bulk": "delete"})
    monkeypatch.setattr(module, "record", lambda *values: audits.append(values))
    module._audit_landed_write(*args)
    assert audits == [args]
    monkeypatch.setattr(module, "record", _fail_audit)
    assert module._audit_landed_write(*args) is None


def test_instance_list_with_health_reads_inside_one_session_in_order(monkeypatch):
    setup_api(monkeypatch)
    calls = []

    class SessionClient(FakePiholeClient):
        def __enter__(self):
            calls.append('enter')
            return self

        def __exit__(self, *_args):
            calls.append('exit')

        def test_connection(self):
            calls.append('connection')

        def get_versions(self):
            calls.append('versions')
            return super().get_versions()

        def get_system_info(self):
            assert calls == ['enter', 'connection', 'versions']
            return super().get_system_info()

        def get_sensors(self):
            assert 'exit' not in calls
            return super().get_sensors()

    monkeypatch.setattr(api, 'PiholeClient', SessionClient)
    response = api.instances_list(health=True)
    row = response['instances'][0]
    assert row['health'] == {
        'status': 'warning',
        'reasons': ['Temperature 57 °C is within 5 °C of the 60 °C limit'],
        'host_uptime': 1296249, 'ftl_uptime': 432620,
        'memory': {'total': 2097152, 'used': 54137, 'available': 2042507, 'percent': 2.581453323364258},
        'swap': {'total': 8388604, 'used': 3017108, 'percent': 35.9667472680794},
        'cpu': {'cores': 4, 'percent': 6.974999904632568, 'load': [0.6064453125, 1.32763671875, 1.234375]},
        'temperature': {'value': 57.0, 'limit': 60.0, 'unit': 'C'},
        'ftl': {'cpu_percent': 0.02500000037252903, 'mem_percent': 1.4299392700195312},
        'database': {'size': 460140544, 'queries': 2636298, 'sqlite_version': '3.53.1'},
    }
    assert FakePiholeClient.info_calls == ['system', 'ftl', 'database', 'sensors']
    assert calls == ['enter', 'connection', 'versions', 'exit']
    assert row['status_detail'] == 'Reachable'
    assert 'secret' not in repr(response)


def test_health_failure_does_not_hide_healthy_connection(monkeypatch):
    setup_api(monkeypatch)

    class FailureClient(FakePiholeClient):
        def get_system_info(self):
            self.info_calls.append('system')
            raise PiholeAPIError('unavailable')

    monkeypatch.setattr(api, 'PiholeClient', FailureClient)
    row = api.instances_list(health=True)['instances'][0]
    assert row['status'] == 'success'
    assert row['versions'] is not None
    assert row['health'] is None
    assert row['status_detail'] == 'Reachable; health details unavailable'
    assert FakePiholeClient.info_calls == ['system']


def test_health_and_version_failures_both_append_detail(monkeypatch):
    setup_api(monkeypatch)

    class FailureClient(FakePiholeClient):
        def get_versions(self):
            raise PiholeAPIError('unavailable')

        def get_system_info(self):
            raise PiholeAPIError('unavailable')

    monkeypatch.setattr(api, 'PiholeClient', FailureClient)
    row = api.instances_list(health=True)['instances'][0]
    assert row['status'] == 'success'
    assert row['health'] is None
    assert row['versions'] is None
    assert row['status_detail'] == 'Reachable; version details unavailable; health details unavailable'


def test_sensors_failure_leaves_temperature_null(monkeypatch):
    setup_api(monkeypatch)

    class FailureClient(FakePiholeClient):
        def get_sensors(self):
            raise PiholeAPIError('unavailable')

    monkeypatch.setattr(api, 'PiholeClient', FailureClient)
    row = api.instances_list(health=True)['instances'][0]
    assert row['health']['temperature'] is None
    assert row['health']['database']['size'] == 460140544
    assert row['status_detail'] == 'Reachable'


def test_health_survives_incompatible_ftl_warning(monkeypatch):
    setup_api(monkeypatch)
    monkeypatch.setattr(FakePiholeClient, 'versions', {
        **FakePiholeClient.versions, 'ftl': {'local': {'version': 'v5.25'}},
    })
    row = api.instances_list(health=True)['instances'][0]
    assert row['status'] == 'warning'
    assert row['health']['ftl_uptime'] == 432620


def test_instance_test_route_omits_health(monkeypatch):
    setup_api(monkeypatch)
    assert set(api.instance_test('pihole1')) == {'status', 'detail', 'versions'}
    assert FakePiholeClient.info_calls == []


@pytest.mark.parametrize('method', ['get_ftl_info', 'get_database_info'])
def test_required_health_reader_failure_is_isolated(monkeypatch, method):
    setup_api(monkeypatch)

    def fail(self):
        raise PiholeAPIError('unavailable')

    monkeypatch.setattr(FakePiholeClient, method, fail)
    row = api.instances_list(health=True)['instances'][0]
    assert row['status'] == 'success'
    assert row['health'] is None
    assert row['status_detail'] == 'Reachable; health details unavailable'


def test_health_missing_credential_and_missing_temperature(monkeypatch):
    _, credentials = setup_api(monkeypatch)
    credentials.delete('pihole1')
    row = api.instances_list(health=True)['instances'][0]
    assert row['status_detail'] == 'No credential stored'
    assert row['health'] is None
    assert FakePiholeClient.info_calls == []
    credentials.set('pihole1', 'secret')
    monkeypatch.setattr(FakePiholeClient, 'sensors_info', SensorsInfo(None, None, 'C'))
    row = api.instances_list(health=True)['instances'][0]
    assert row['health']['temperature'] is None
    assert row['status_detail'] == 'Reachable'


def test_health_transport_error_remains_unreachable(monkeypatch):
    setup_api(monkeypatch)

    def fail(self):
        raise httpx.ConnectError('secret transport internals')

    monkeypatch.setattr(FakePiholeClient, 'get_sensors', fail)
    row = api.instances_list(health=True)['instances'][0]
    assert row['status'] == 'error'
    assert row['health'] is None
    assert row['status_detail'] == 'Unable to reach Pi-hole instance'
    assert 'secret' not in repr(row)


def test_health_query_flag_authentication_and_read_only_refresh(monkeypatch, tmp_path):
    import asyncio

    from fastapi import FastAPI
    from app.auth import AuthConfig, AuthManager
    from app.auth_middleware import AuthenticationMiddleware
    from app import audit

    setup_api(monkeypatch)
    audit_file = tmp_path / 'audit.jsonl'
    audit_file.write_text('')
    original_mtime = audit_file.stat().st_mtime_ns
    monkeypatch.setattr(audit, 'AUDIT_FILE', audit_file)
    monkeypatch.setattr(api, 'record', audit.record)
    manager = AuthManager(tmp_path / 'auth.db', AuthConfig(
        mode='proxy', proxy_users=frozenset({'operator'}), proxy_sources=('127.0.0.1',),
    ))
    test_app = FastAPI()
    test_app.include_router(api.router)
    test_app.add_middleware(AuthenticationMiddleware, manager=manager)

    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url='http://test') as client:
            assert (await client.get('/api/instances?health=true')).status_code == 401
            headers = {'Remote-User': 'operator'}
            assert (await client.get('/api/instances?health=abc', headers=headers)).status_code == 422
            assert FakePiholeClient.info_calls == []
    asyncio.run(requests())
    assert api.instances_list(health=False)['instances'][0]['health'] is None
    assert FakePiholeClient.info_calls == []
    for _ in range(2):
        assert api.instances_list(health=True)['instances'][0]['health']['ftl_uptime'] == 432620
    assert audit_file.stat().st_mtime_ns == original_mtime
    assert audit_file.read_text() == ''


def healthy_system() -> SystemInfo:
    return replace(FakePiholeClient.system_info)


def quiet_sensors() -> SensorsInfo:
    return SensorsInfo(40.0, 60.0, "C")


def test_health_status_healthy_when_nothing_trips():
    assert api._health_status(healthy_system(), quiet_sensors()) == ("healthy", [])


@pytest.mark.parametrize("cpu_temp,hot_limit,expected_status,reasons", [
    (54.9, 60, "healthy", []),
    (55, 60, "warning", ["Temperature 55 °C is within 5 °C of the 60 °C limit"]),
    (59.9, 60, "warning", ["Temperature 60 °C is within 5 °C of the 60 °C limit"]),
    (60, 60, "critical", ["Temperature 60 °C is at or above the 60 °C limit"]),
    (75, 60, "critical", ["Temperature 75 °C is at or above the 60 °C limit"]),
])
def test_health_status_temperature_band(cpu_temp, hot_limit, expected_status, reasons):
    sensors = replace(quiet_sensors(), cpu_temp=cpu_temp, hot_limit=hot_limit)
    assert api._health_status(healthy_system(), sensors) == (expected_status, reasons)


@pytest.mark.parametrize("sensors", [SensorsInfo(90.0, None, "C"), SensorsInfo(None, 60.0, "C"), None])
def test_health_status_skips_temperature_without_limit_or_sensors(sensors):
    assert api._health_status(healthy_system(), sensors) == ("healthy", [])


@pytest.mark.parametrize("percent,status,reasons", [
    (79.9, "healthy", []),
    (80, "warning", ["RAM 80.0% used, at or above 80%"]),
    (95, "critical", ["RAM 95.0% used, at or above 95%"]),
])
def test_health_status_ram_band(percent, status, reasons):
    assert api._health_status(replace(healthy_system(), ram_percent=percent), quiet_sensors()) == (status, reasons)


@pytest.mark.parametrize("percent,status,reasons", [
    (49.9, "healthy", []),
    (50, "warning", ["Swap 50.0% used, at or above 50%"]),
    (100, "warning", ["Swap 100.0% used, at or above 50%"]),
])
def test_health_status_swap_warns_only(percent, status, reasons):
    assert api._health_status(replace(healthy_system(), swap_percent=percent), quiet_sensors()) == (status, reasons)


@pytest.mark.parametrize("cores,load,status,reasons", [
    (4, 3.99, "healthy", []),
    (4, 4.0, "warning", ["Load 4.00 is 1.0 per core, at or above 1.0"]),
    (4, 8.0, "critical", ["Load 8.00 is 2.0 per core, at or above 2.0"]),
    (0, 99, "healthy", []),
])
def test_health_status_load_per_core(cores, load, status, reasons):
    system = replace(healthy_system(), cores=cores, load=(load, 1.32763671875, 1.234375))
    assert api._health_status(system, quiet_sensors()) == (status, reasons)


def test_health_status_critical_outranks_warning_and_keeps_order():
    system = replace(healthy_system(), ram_percent=85, swap_percent=60, load=(9.0, 1.32763671875, 1.234375))
    assert api._health_status(system, SensorsInfo(61, 60, "C")) == ("critical", [
        "Temperature 61 °C is at or above the 60 °C limit",
        "RAM 85.0% used, at or above 80%",
        "Swap 60.0% used, at or above 50%",
        "Load 9.00 is 2.2 per core, at or above 2.0",
    ])


def test_health_status_in_route_payload(monkeypatch):
    setup_api(monkeypatch)
    monkeypatch.setattr(FakePiholeClient, "system_info", replace(healthy_system(), ram_percent=96.0))
    monkeypatch.setattr(FakePiholeClient, "sensors_info", SensorsInfo(None, None, "C"))
    row = api.instances_list(health=True)["instances"][0]
    assert row["health"]["status"] == "critical"
    assert row["health"]["reasons"] == ["RAM 96.0% used, at or above 95%"]
    assert row["status"] == "success"
    assert row["status_detail"] == "Reachable"
    assert "secret" not in repr(row)


def test_instance_backup_returns_archive_and_audits(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    archive = FakePiholeClient.teleporter
    response = api.instance_backup("pihole1")
    assert response.body == archive
    assert response.media_type == "application/zip"
    assert re.fullmatch(r'attachment; filename="pihole-pihole1-\d{8}-\d{6}Z\.zip"', response.headers["content-disposition"])
    assert response.headers["cache-control"] == "no-store"
    assert audits == [("pihole1", "backup_download", {}, {
        "bytes": len(archive), "sha256": hashlib.sha256(archive).hexdigest(),
    })]


def test_instance_backup_unknown_instance_404(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    with pytest.raises(HTTPException) as caught:
        api.instance_backup("missing")
    assert caught.value.status_code == 404
    assert caught.value.detail == "Instance not found"
    assert audits == []


def test_instance_backup_without_credential_409(monkeypatch):
    _, credentials = setup_api(monkeypatch)
    credentials.delete("pihole1")
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    with pytest.raises(HTTPException) as caught:
        api.instance_backup("pihole1")
    assert caught.value.status_code == 409
    assert caught.value.detail == "No credential stored"
    assert audits == []


def test_instance_backup_pihole_error_502(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))

    class BrokenClient(FakePiholeClient):
        def get_teleporter(self):
            raise PiholeAPIError("GET /api/teleporter failed with HTTP 500")

    monkeypatch.setattr(api, "PiholeClient", BrokenClient)
    with pytest.raises(HTTPException) as caught:
        api.instance_backup("pihole1")
    assert caught.value.status_code == 502
    assert caught.value.detail == "GET /api/teleporter failed with HTTP 500"
    assert audits == []


def test_instance_backup_fails_closed_when_audit_fails(monkeypatch):
    setup_api(monkeypatch)

    def fail_record(*_args):
        raise OSError("Audit unavailable")

    monkeypatch.setattr(api, "record", fail_record)
    with pytest.raises(HTTPException) as caught:
        api.instance_backup("pihole1")
    assert caught.value.status_code == 503
    assert caught.value.detail == "Backup not delivered: the audit log could not be written"


def test_instance_backup_route_is_post_only():
    routes = [route for route in api.router.routes if route.path == "/api/instances/{name}/backup"]
    assert len(routes) == 1
    assert routes[0].methods == {"POST"}


def test_backup_filename_slugs_instance_name():
    now = datetime(2026, 9, 25, 14, 3, 7, tzinfo=timezone.utc)
    assert api._backup_filename("lab pihole/1", now) == "pihole-lab-pihole-1-20260925-140307Z.zip"
    assert api._backup_filename("../", now) == "pihole-instance-20260925-140307Z.zip"


@pytest.mark.parametrize('action', ['restart_dns', 'flush_logs', 'flush_network'])
def test_instance_action_runs_and_audits(monkeypatch, action):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, 'record', lambda *args: audits.append(args))
    assert api.instance_action('pihole1', action) == {'instance': 'pihole1', 'action': action}
    assert FakePiholeClient.actions == [action]
    assert audits == [('pihole1', f'ftl_{action}', {}, {'status': 'success'})]


def test_instance_action_unknown_instance_404(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, 'record', lambda *args: audits.append(args))
    with pytest.raises(HTTPException) as caught:
        api.instance_action('missing', 'restart_dns')
    assert caught.value.status_code == 404
    assert caught.value.detail == 'Instance not found'
    assert FakePiholeClient.actions == []
    assert audits == []


def test_instance_action_without_credential_409(monkeypatch):
    _, credentials = setup_api(monkeypatch)
    credentials.delete('pihole1')
    audits = []
    monkeypatch.setattr(api, 'record', lambda *args: audits.append(args))
    with pytest.raises(HTTPException) as caught:
        api.instance_action('pihole1', 'restart_dns')
    assert caught.value.status_code == 409
    assert caught.value.detail == 'No credential stored'
    assert FakePiholeClient.actions == []
    assert audits == []


def test_instance_action_pihole_error_502(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, 'record', lambda *args: audits.append(args))
    detail = 'Pi-hole refused the action (HTTP 403); check webserver.api.allow_destructive on the instance'

    class BrokenClient(FakePiholeClient):
        def run_action(self, action):
            raise PiholeAPIError(detail, status_code=403)

    monkeypatch.setattr(api, 'PiholeClient', BrokenClient)
    with pytest.raises(HTTPException) as caught:
        api.instance_action('pihole1', 'restart_dns')
    assert caught.value.status_code == 502
    assert caught.value.detail == detail
    assert audits == []


def test_instance_action_unreachable_before_send_503_not_audited(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, 'record', lambda *args: audits.append(args))

    class BrokenClient(FakePiholeClient):
        def __enter__(self):
            raise httpx.ConnectError('Connection refused')

    monkeypatch.setattr(api, 'PiholeClient', BrokenClient)
    with pytest.raises(HTTPException) as caught:
        api.instance_action('pihole1', 'restart_dns')
    assert caught.value.status_code == 503
    assert caught.value.detail == 'Unable to reach Pi-hole instance'
    assert FakePiholeClient.actions == []
    assert audits == []


def test_instance_action_transport_error_after_send_audits_unconfirmed(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))

    class BrokenClient(FakePiholeClient):
        def run_action(self, action):
            self.actions.append(action)
            raise httpx.ReadError("Connection reset")

    monkeypatch.setattr(api, "PiholeClient", BrokenClient)
    with pytest.raises(HTTPException) as caught:
        api.instance_action("pihole1", "restart_dns")
    assert caught.value.status_code == 503
    assert caught.value.detail == "The action was sent but Pi-hole did not confirm it; it may have run. Check the instance."
    assert FakePiholeClient.actions == ["restart_dns"]
    assert audits == [("pihole1", "ftl_restart_dns", {}, {"status": "unconfirmed", "reason": "transport_error"})]


@pytest.mark.parametrize(("status_code", "detail", "reason"), [
    (500, "POST /api/action/flush/logs failed with HTTP 500", "http_500"),
    (200, "Pi-hole action endpoint returned an invalid response", "invalid_response"),
])
def test_instance_action_upstream_error_after_send_audits_unconfirmed(monkeypatch, status_code, detail, reason):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))

    class BrokenClient(FakePiholeClient):
        def run_action(self, action):
            self.actions.append(action)
            raise pihole_client.PiholeActionUnconfirmedError(detail, status_code=status_code)

    monkeypatch.setattr(api, "PiholeClient", BrokenClient)
    with pytest.raises(HTTPException) as caught:
        api.instance_action("pihole1", "flush_logs")
    assert caught.value.status_code == 502
    assert caught.value.detail == f"{detail}; the action may have partly run. Check the instance."
    assert FakePiholeClient.actions == ["flush_logs"]
    assert audits == [("pihole1", "ftl_flush_logs", {}, {"status": "unconfirmed", "reason": reason})]


def test_instance_action_unconfirmed_survives_audit_failure(monkeypatch):
    setup_api(monkeypatch)

    def fail_record(*_args):
        raise OSError("Audit unavailable")

    class BrokenClient(FakePiholeClient):
        def run_action(self, action):
            self.actions.append(action)
            raise pihole_client.PiholeActionUnconfirmedError(
                "POST /api/action/flush/logs failed with HTTP 500", status_code=500,
            )

    monkeypatch.setattr(api, "record", fail_record)
    monkeypatch.setattr(api, "PiholeClient", BrokenClient)
    with pytest.raises(HTTPException) as caught:
        api.instance_action("pihole1", "flush_logs")
    assert caught.value.status_code == 502
    assert caught.value.detail == (
        "POST /api/action/flush/logs failed with HTTP 500; "
        "the action may have partly run. Check the instance."
    )
    assert FakePiholeClient.actions == ["flush_logs"]


def test_instance_action_survives_audit_failure(monkeypatch):
    setup_api(monkeypatch)

    def fail_record(*_args):
        raise OSError('Audit unavailable')

    monkeypatch.setattr(api, 'record', fail_record)
    assert api.instance_action('pihole1', 'flush_network') == {'instance': 'pihole1', 'action': 'flush_network'}
    assert FakePiholeClient.actions == ['flush_network']


def test_instance_action_route_is_post_only_with_literal_action(monkeypatch):
    import asyncio
    from fastapi import FastAPI

    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, 'record', lambda *args: audits.append(args))
    routes = [route for route in api.router.routes if route.path == '/api/instances/{name}/actions/{action}']
    assert len(routes) == 1
    assert routes[0].methods == {'POST'}
    test_app = FastAPI()
    test_app.include_router(api.router)

    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url='http://test') as client:
            response = await client.post('/api/instances/pihole1/actions/unknown')
            assert response.status_code == 422

    asyncio.run(requests())
    assert FakePiholeClient.actions == []
    assert audits == []


def test_adlist_key_keeps_userinfo_and_fragment():
    assert api._adlist_key("https://alice:one@example.com/list") != api._adlist_key("https://bob:two@example.com/list")
    assert api._adlist_key("https://example.com/l#a") != api._adlist_key("https://example.com/l#b")
    assert api._adlist_key("HTTPS://user@Example.COM/l/") == api._adlist_key("https://user@example.com/l")


def test_adlist_delete_targets_the_exact_credentialed_record(bulk_client, monkeypatch):
    alice = _bulk_adlist("https://alice:one@example.com/list")
    bob = _bulk_adlist("https://bob:two@example.com/list")
    bulk_client.adlists = [alice, bob]
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    api.adlist_delete("pihole1", api.AdlistDeleteInput(address=bob.address))
    assert bulk_client.deletes == [bob.address]
    assert FakePiholeClient.adlists == [alice]
    assert len(audits) == 1
    assert audits[0][1] == "adlist_delete"
    assert audits[0][2]["address"] == bob.address


def test_adlist_delete_does_not_match_a_bare_address_to_a_credentialed_record(bulk_client, monkeypatch):
    items = [_bulk_adlist("https://alice:one@example.com/list"), _bulk_adlist("https://bob:two@example.com/list")]
    bulk_client.adlists = items
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    with pytest.raises(HTTPException) as exc:
        api.adlist_delete("pihole1", api.AdlistDeleteInput(address="https://example.com/list"))
    assert exc.value.status_code == 404
    assert bulk_client.deletes == []
    assert bulk_client.adlists == items
    assert audits == []
    assert "pihole1" not in api.load_pending_gravity()


def test_adlist_delete_rejects_an_ambiguous_canonical_match(bulk_client, monkeypatch):
    items = [_bulk_adlist("https://example.com/l"), _bulk_adlist("https://example.com/l/")]
    bulk_client.adlists = items
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    for operation, payload in [
        (api.adlist_delete, api.AdlistDeleteInput(address="HTTPS://EXAMPLE.COM/l")),
        (api.adlist_update, api.AdlistUpdateInput(address="HTTPS://EXAMPLE.COM/l", enabled=False)),
    ]:
        with pytest.raises(HTTPException) as exc:
            operation("pihole1", payload)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Address matches more than one block list on this instance; use the exact stored address"
    assert bulk_client.deletes == []
    assert bulk_client.updates == []
    assert bulk_client.adlists == items
    assert audits == []
    assert "pihole1" not in api.load_pending_gravity()


def test_adlist_exact_match_beats_a_canonical_sibling(bulk_client):
    items = [_bulk_adlist("https://example.com/l"), _bulk_adlist("https://example.com/l/")]
    bulk_client.adlists = items
    for item in items:
        api.adlist_update("pihole1", api.AdlistUpdateInput(address=item.address, enabled=False))
    assert bulk_client.updates == [(item, False, [0]) for item in items]


def test_adlist_bulk_keeps_credentialed_records_distinct(bulk_client):
    addresses = ["https://alice:one@example.com/list", "https://bob:two@example.com/list"]
    bulk_client.adlists = [_bulk_adlist(address) for address in addresses]
    result = _adlist_batch("delete", addresses)
    assert bulk_client.deletes == addresses
    assert FakePiholeClient.adlists == []
    assert result["results"] == [
        {"id": address, "success": True, "changed": True, "error": None} for address in addresses
    ]


def test_adlist_bulk_reports_an_ambiguous_address(bulk_client, monkeypatch):
    items = [_bulk_adlist("https://example.com/l"), _bulk_adlist("https://example.com/l/")]
    bulk_client.adlists = items
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    result = _adlist_batch("disable", ["HTTPS://EXAMPLE.COM/l"])
    assert result["results"] == [{
        "id": "HTTPS://EXAMPLE.COM/l", "success": False, "changed": False,
        "error": "Matches more than one block list on this instance; use the exact stored address",
    }]
    assert bulk_client.updates == []
    assert bulk_client.adlists == items
    assert audits == []
    assert result["gravity_pending"] is False


def test_adlist_bulk_collapses_literal_duplicates_silently(bulk_client):
    address = "https://lists.example/block.txt"
    result = _adlist_batch("delete", [address, address])
    assert bulk_client.deletes == [address]
    assert result["results"] == [{"id": address, "success": True, "changed": True, "error": None}]


def test_version_route_returns_app_version() -> None:
    from app import __version__

    assert api.version().model_dump() == {"version": __version__}


def test_settings_apply_input_requires_expected():
    with pytest.raises(ValidationError):
        api.SettingsApplyInput(values={})
    assert api.SettingsInput(values={}).values == {}


def _assert_settings_drift(monkeypatch, scenario, values, expected):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    old_logging = FakePiholeClient.tree["dns"]["queryLogging"]
    old_privacy = FakePiholeClient.tree["misc"]["privacylevel"]
    try:
        if scenario in {"untouched", "edited"}:
            FakePiholeClient.tree["misc"]["privacylevel"] = 2
        if scenario == "landed":
            FakePiholeClient.tree["dns"]["queryLogging"] = False
        with pytest.raises(HTTPException) as caught:
            api.settings_apply("pihole1", api.SettingsApplyInput(values=values, expected=expected))
        assert caught.value.status_code == 409
        assert caught.value.detail == "Settings on pihole1 changed since the review. Review the changes again."
        assert FakePiholeClient.patches == []
        assert audits == []
    finally:
        FakePiholeClient.tree["dns"]["queryLogging"] = old_logging
        FakePiholeClient.tree["misc"]["privacylevel"] = old_privacy


def test_settings_apply_proceeds_when_expected_matches(monkeypatch):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    result = api.settings_apply("pihole1", api.SettingsApplyInput(
        values={"dns.queryLogging": False}, expected={"dns.queryLogging": True},
    ))
    assert result["applied"] == 1
    assert FakePiholeClient.patches == [{"dns": {"queryLogging": False}}]
    assert audits == [("pihole1", "settings_change", {"dns.queryLogging": True}, {"dns.queryLogging": False})]


def _assert_settings_patch_failure(monkeypatch, error, status, detail):
    setup_api(monkeypatch)
    audits = []
    monkeypatch.setattr(api, "record", lambda *args: audits.append(args))
    def fail_patch(self, patch):
        raise error
    monkeypatch.setattr(FakePiholeClient, "patch_config", fail_patch)
    with pytest.raises(HTTPException) as caught:
        api.settings_apply("pihole1", api.SettingsApplyInput(
            values={"dns.queryLogging": False}, expected={"dns.queryLogging": True},
        ))
    assert caught.value.status_code == status
    assert detail in caught.value.detail
    assert "boom" not in caught.value.detail
    assert audits == []


def test_dns_add_rejects_ipv6_duplicate_spelled_differently(monkeypatch):
    setup_api(monkeypatch)
    original = FakePiholeClient.tree["dns"]["hosts"]
    try:
        FakePiholeClient.tree["dns"]["hosts"] = ["fd00::1 box.lan"]
        with pytest.raises(HTTPException) as caught:
            api.dns_record_add(api.DNSRecordInput(instance="pihole1", hostname="box.lan", address="fd00:0:0:0:0:0:0:1"))
        assert caught.value.status_code == 409
        assert FakePiholeClient.added_hosts == []
    finally:
        FakePiholeClient.tree["dns"]["hosts"] = original


def test_dns_delete_matches_ipv6_spelled_differently(monkeypatch):
    setup_api(monkeypatch)
    original = FakePiholeClient.tree["dns"]["hosts"]
    try:
        FakePiholeClient.tree["dns"]["hosts"] = ["fd00::1 box.lan nas.lan"]
        api.dns_record_delete(api.DNSRecordInput(instance="pihole1", hostname="box.lan", address="fd00:0::1"))
        assert FakePiholeClient.deleted_hosts == ["fd00::1 box.lan nas.lan"]
        assert FakePiholeClient.added_hosts == ["fd00::1 nas.lan"]
    finally:
        FakePiholeClient.tree["dns"]["hosts"] = original


def test_dns_update_ipv6_respelling_is_a_noop(monkeypatch):
    setup_api(monkeypatch)
    original = FakePiholeClient.tree["dns"]["hosts"]
    try:
        FakePiholeClient.tree["dns"]["hosts"] = ["fd00::1 box.lan"]
        result = api.dns_record_update(api.DNSRecordUpdate(instance="pihole1", old_hostname="box.lan", hostname="box.lan", old_address="fd00::1", address="fd00:0:0:0:0:0:0:1"))
        assert result == {"changed": False, "record": "fd00:0:0:0:0:0:0:1 box.lan"}
        assert FakePiholeClient.added_hosts == FakePiholeClient.deleted_hosts == []
    finally:
        FakePiholeClient.tree["dns"]["hosts"] = original


def test_dns_update_moves_record_to_ipv6(monkeypatch):
    setup_api(monkeypatch)
    calls = []
    monkeypatch.setattr(FakePiholeClient, "add_dns_host", lambda self, raw: calls.append(("add", raw)))
    monkeypatch.setattr(FakePiholeClient, "delete_dns_host", lambda self, raw: calls.append(("delete", raw)))
    api.dns_record_update(api.DNSRecordUpdate(instance="pihole1", old_hostname="server.lan", hostname="server.lan", old_address="192.168.1.10", address="fd00::10"))
    assert calls == [("add", "fd00::10 server.lan"), ("delete", "192.168.1.10 server.lan")]


def test_dns_update_rejects_stale_original(monkeypatch):
    setup_api(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        api.dns_record_update(api.DNSRecordUpdate(instance="pihole1", old_hostname="missing.lan", hostname="server.lan", old_address="192.168.1.99", address="fd00::10"))
    assert caught.value.status_code == 409
    assert caught.value.detail == "The original record changed; refresh and try again"
    assert FakePiholeClient.added_hosts == FakePiholeClient.deleted_hosts == []


def test_dns_records_list_reports_unreachable_instance(monkeypatch):
    instances, _ = setup_api(monkeypatch)
    instances.append(Instance(name="pihole2", base_url="http://pihole2"))
    def settings_tree(instance):
        if instance.name == "pihole2":
            raise HTTPException(status_code=503, detail="Unable to reach Pi-hole instance")
        return FakePiholeClient.tree
    monkeypatch.setattr(api, "_settings_tree", settings_tree)
    result = api.dns_records_list()
    assert result["errors"] == {"pihole2": "Unable to reach Pi-hole instance"}
    assert result["records"][0]["hostname"] == "server.lan"
    assert "pihole1" in result["records"][0]["instances"]
    assert result["records"][0]["state"] == "consistent"


def test_adlists_list_reports_unreachable_instance(monkeypatch):
    instances, _ = setup_api(monkeypatch)
    instances.append(Instance(name="pihole2", base_url="http://pihole2"))
    def client(instance):
        if instance.name == "pihole2":
            raise httpx.ConnectError("boom")
        return FakePiholeClient()
    monkeypatch.setattr(api, "_client", client)
    result = api.adlists_list()
    assert result["errors"] == {"pihole2": "Unable to reach Pi-hole instance"}
    assert result["adlists"][0]["address"] == "https://lists.example/block.txt"
    assert "pihole1" in result["adlists"][0]["instances"]
    assert "boom" not in repr(result)


def test_settings_apply_rejects_value_changed_since_review(monkeypatch):
    _assert_settings_drift(monkeypatch, "untouched",
                           {"dns.queryLogging": False, "misc.privacylevel": 0},
                           {"dns.queryLogging": True})
    _assert_settings_drift(monkeypatch, "edited", {"misc.privacylevel": 1}, {"misc.privacylevel": 0})


def test_settings_apply_rejects_change_that_already_landed(monkeypatch):
    _assert_settings_drift(monkeypatch, "landed", {"dns.queryLogging": False}, {"dns.queryLogging": True})


def test_settings_apply_drift_check_is_type_strict(monkeypatch):
    _assert_settings_drift(monkeypatch, "strict", {"misc.privacylevel": 1}, {"misc.privacylevel": False})


def test_settings_apply_rejected_patch_is_502_and_not_audited(monkeypatch):
    _assert_settings_patch_failure(monkeypatch,
        PiholeAPIError("Config item dns.queryLogging is read-only", status_code=400),
        502, "Config item dns.queryLogging is read-only")


def test_settings_apply_transport_failure_is_503_and_not_audited(monkeypatch):
    _assert_settings_patch_failure(monkeypatch, httpx.ConnectError("boom"),
                                   503, "Unable to reach Pi-hole instance")


def test_instances_count_reports_configured_instances(monkeypatch):
    setup_api(monkeypatch)
    assert api.instances_count().count == 1

    api.instance_save(api.InstanceInput(name="pihole2", base_url="http://pihole2", app_password="secret2"))

    assert api.instances_count().count == 2


def test_instances_count_is_zero_without_instances(monkeypatch):
    monkeypatch.setattr(api, "load_instances", lambda: [])

    assert api.instances_count().count == 0


def test_instances_count_does_not_contact_pihole(monkeypatch):
    setup_api(monkeypatch)

    class UnexpectedPiholeClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Instance count must not contact Pi-hole")

    monkeypatch.setattr(api, "PiholeClient", UnexpectedPiholeClient)

    assert api.instances_count().count == 1


def test_instances_count_route_is_registered():
    assert any(
        getattr(route, "path", None) == "/api/instances/count" and "GET" in route.methods
        for route in api.router.routes
    )


def _fail_single_route_audit(*_args):
    raise OSError("audit unavailable")


@pytest.mark.parametrize("operation", ["blocking", "add", "update", "delete", "gravity"])
def test_blocking_and_adlist_routes_report_success_when_audit_fails(monkeypatch, caplog, operation):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    setup_api(monkeypatch)
    monkeypatch.setattr(api, "record", _fail_single_route_audit)
    application = FastAPI()
    application.include_router(api.router)
    address = "https://lists.example/block.txt"
    with TestClient(application) as client:
        if operation == "blocking":
            response = client.post("/api/blocking", json={"instances": ["pihole1"], "enabled": False})
            assert response.json()["results"][0]["success"] is True
            assert FakePiholeClient.blocking_changes == [(False, None)]
        elif operation == "add":
            response = client.post("/api/adlists", json={"instances": ["pihole1"], "address": "https://lists.example/new"})
            assert response.json()["results"][0]["success"] is True
            assert FakePiholeClient.adlists[-1].address == "https://lists.example/new"
        elif operation == "update":
            response = client.patch("/api/adlists/pihole1", json={"address": address, "enabled": False})
            assert FakePiholeClient.adlists[0].enabled is False
        elif operation == "delete":
            response = client.request("DELETE", "/api/adlists/pihole1", json={"address": address})
            assert FakePiholeClient.adlists == []
        else:
            api.mark_gravity_pending("pihole1")
            response = client.post("/api/adlists/pihole1/gravity")
            assert response.json()["gravity_pending"] is False
            assert FakePiholeClient.gravity_updates == 1
        if operation != "blocking":
            assert api.load_pending_gravity() == (set() if operation == "gravity" else {"pihole1"})
    assert 200 <= response.status_code < 300
    assert any(item.name == api.__name__ and item.exc_info and "audit write failed" in item.message for item in caplog.records)


@pytest.mark.parametrize("cname", [False, True])
@pytest.mark.parametrize("operation", ["add", "update", "delete"])
def test_dns_routes_report_success_when_audit_fails(monkeypatch, caplog, operation, cname):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    setup_api(monkeypatch)
    monkeypatch.setattr(api, "record", _fail_single_route_audit)
    application = FastAPI()
    application.include_router(api.router)
    if cname:
        old = {"hostname": "alias.lan", "target": "server.lan"}
        new = {"hostname": "new.lan", "target": "server.lan"}
        old_line, new_line = "alias.lan,server.lan", "new.lan,server.lan"
        added, deleted = FakePiholeClient.added_cnames, FakePiholeClient.deleted_cnames
    else:
        old = {"hostname": "server.lan", "address": "192.168.1.10"}
        new = {"hostname": "new.lan", "address": "192.168.1.11"}
        old_line, new_line = "192.168.1.10 server.lan", "192.168.1.11 new.lan"
        added, deleted = FakePiholeClient.added_hosts, FakePiholeClient.deleted_hosts
    payload = {"instance": "pihole1", **(old if operation == "delete" else new)}
    if operation == "update":
        payload.update({f"old_{key}": value for key, value in old.items()})
    with TestClient(application) as client:
        response = client.request({"add": "POST", "update": "PATCH", "delete": "DELETE"}[operation],
                                  "/api/dns/" + ("cnames" if cname else "records"), json=payload)
    assert 200 <= response.status_code < 300
    if operation == "delete":
        assert response.json() == {"deleted": old_line}
    elif operation == "update":
        assert response.json() == {"changed": True, "record": new_line}
    else:
        assert response.json() == {"instance": "pihole1", "record": new_line}
    assert added == ([] if operation == "delete" else [new_line])
    assert deleted == ([] if operation == "add" else [old_line])
    assert any(item.name == api.__name__ and item.exc_info and "audit write failed" in item.message for item in caplog.records)


def test_settings_apply_reports_success_when_audit_fails(monkeypatch, caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    setup_api(monkeypatch)
    monkeypatch.setattr(api, "record", _fail_single_route_audit)
    application = FastAPI()
    application.include_router(api.router)
    payload = {"values": {"dns.queryLogging": False}, "expected": {"dns.queryLogging": True}}
    expected_changes = api.settings_diff("pihole1", api.SettingsInput(values=payload["values"]))["changes"]
    with TestClient(application) as client:
        response = client.post("/api/settings/pihole1/apply", json=payload)
    assert response.status_code == 200
    assert response.json() == {"applied": 1, "changes": expected_changes}
    assert FakePiholeClient.patches == [{"dns": {"queryLogging": False}}]
    assert any(item.name == api.__name__ and item.exc_info and "audit write failed" in item.message for item in caplog.records)


@pytest.mark.parametrize("cname", [False, True])
def test_partial_dns_failure_detail_survives_audit_failure(monkeypatch, caplog, cname):
    setup_api(monkeypatch)
    monkeypatch.setattr(api, "record", _fail_single_route_audit)

    class FailingClient(FakePiholeClient):
        def add_dns_cname(self, record):
            raise PiholeAPIError("add failed")

        def delete_dns_host(self, record):
            raise PiholeAPIError("delete failed")

    monkeypatch.setattr(api, "PiholeClient", FailingClient)
    with pytest.raises(HTTPException) as caught:
        if cname:
            api.dns_cname_update(api.CNAMERecordUpdate(
                instance="pihole1", old_hostname="alias.lan", old_target="server.lan",
                hostname="alias.lan", target="new.lan",
            ))
        else:
            api.dns_record_update(api.DNSRecordUpdate(
                instance="pihole1", old_hostname="server.lan", old_address="192.168.1.10",
                hostname="server.lan", address="192.168.1.11",
            ))
    assert caught.value.status_code == 502
    detail = ("'alias.lan,server.lan' was removed and could not be restored" if cname else
              "'192.168.1.11 server.lan' was added and could not be rolled back")
    assert detail in caught.value.detail
    assert any(item.name == api.__name__ and item.exc_info and "audit write failed" in item.message for item in caplog.records)
