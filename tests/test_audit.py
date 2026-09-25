from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app import audit
from app.auth import AuthConfig, AuthManager
from app.auth_middleware import AuthenticationMiddleware


@pytest.fixture(autouse=True)
def audit_file(tmp_path, monkeypatch):
    path = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "AUDIT_FILE", path)
    monkeypatch.setattr(audit, "DATA_DIR", tmp_path)
    return path


def write_entries(path, *entries):
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def entry(ts="1", instance="lab-pihole", action="dns_update", actor="admin", **values):
    return dict(ts=ts, instance=instance, action=action, actor=actor, before={}, after={}, **values)


def test_search_returns_newest_first_with_facets(audit_file):
    rows = [entry("3"), entry("2", instance="other"), entry("1", instance="application", action="login", actor=None)]
    write_entries(audit_file, *rows)
    result = audit.search()
    assert result == dict(entries=rows[::-1], matched=3, skipped=0,
                         instances=["application", "lab-pihole", "other"],
                         actions=["dns_update", "login"], actors=["admin"])


def test_search_missing_file_returns_empty():
    assert audit.search() == dict(entries=[], matched=0, skipped=0, instances=[], actions=[], actors=[])


def test_search_exact_filters_combine(audit_file):
    rows = [entry(), entry(instance="other"), entry(action="login"), entry(actor="other")]
    write_entries(audit_file, *rows)
    result = audit.search(instance="lab-pihole", action="dns_update", actor="admin")
    assert result["entries"] == rows[:1]
    assert result["actors"] == ["admin", "other"]
    assert audit.search(instance="lab")["matched"] == 0


def test_search_text_matches_nested_values_case_insensitively(audit_file):
    row = entry()
    row["after"] = {"dns.hosts": "192.168.1.10 nas.lan", "nested": [{"regex": r"(\.|^)com$", "number": 42, "enabled": True}]}
    row["before"] = {"remote_addr": "127.0.0.1"}
    write_entries(audit_file, row)
    for text in ["NAS.LAN", r"(\.|^)com$", "remote_addr", "LAB-PIHOLE", "DNS_UPDATE", "ADMIN", "42", "true"]:
        assert audit.search(text=text)["entries"] == [row]
    assert audit.search(text="missing")["matched"] == 0


def test_search_limit_caps_entries_but_counts_every_match(audit_file):
    rows = [entry(str(i)) for i in range(5)]
    write_entries(audit_file, *rows)
    result = audit.search(limit=2)
    assert result["entries"] == rows[-2:][::-1]
    assert result["matched"] == 5


def test_search_skips_malformed_and_partial_lines(audit_file):
    row = entry()
    audit_file.write_text(json.dumps(row) + '\n\nnot json\n[]\n  \n{"ts": "2026-')
    result = audit.search()
    assert result["entries"] == [row]
    assert result["skipped"] == 3


def test_search_defaults_missing_fields(audit_file):
    write_entries(audit_file, {"action": "login"}, {"before": "bad", "after": []})
    assert audit.search()["entries"] == [
        dict(ts="", instance="", action="", actor=None, before={}, after={}),
        dict(ts="", instance="", action="login", actor=None, before={}, after={}),
    ]


def test_search_round_trips_record(monkeypatch):
    monkeypatch.setattr(audit, "get_current_user", lambda: "admin")
    audit.record("lab", "first", {}, {"a": 1})
    audit.record("lab", "second", {"a": 1}, {})
    rows = audit.search()["entries"]
    assert [row["action"] for row in rows] == ["second", "first"]
    assert [row["actor"] for row in rows] == ["admin", "admin"]
    assert rows[0]["before"] == rows[1]["after"] == {"a": 1}


def test_audit_log_route_echoes_filters_and_returned(audit_file):
    from app.routes.audit_log import audit_log

    write_entries(audit_file, entry())
    result = audit_log(instance=" lab-pihole ", action=" dns_update ", actor=" admin ", text=" LAB ", limit=1)
    assert {key: result[key] for key in ("instance", "action", "actor", "text", "limit")} == dict(instance="lab-pihole", action="dns_update", actor="admin", text="LAB", limit=1)
    assert result["returned"] == len(result["entries"]) == 1


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 1001}, {"text": "x" * 201}])
def test_audit_log_route_rejects_bad_params(kwargs):
    from app.routes.audit_log import audit_log

    with pytest.raises(HTTPException) as exc:
        audit_log(**kwargs)
    assert exc.value.status_code == 422
    assert isinstance(exc.value.detail, str)


def test_audit_log_route_unreadable_file_returns_503(audit_file, monkeypatch):
    from app.routes.audit_log import audit_log

    def fail(**kwargs):
        raise PermissionError(str(audit_file))

    monkeypatch.setattr(audit, "search", fail)
    with pytest.raises(HTTPException) as exc:
        audit_log()
    assert exc.value.status_code == 503
    assert exc.value.detail == "Audit log could not be read"
    assert str(audit_file) not in exc.value.detail


def test_audit_log_route_requires_authentication(tmp_path, monkeypatch):
    from app.routes.audit_log import router

    calls = []
    monkeypatch.setattr(audit, "search", lambda **kwargs: calls.append(kwargs))
    manager = AuthManager(tmp_path / "auth.db", AuthConfig(mode="local", cookie_secure=False))
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware, manager=manager)
    app.include_router(router)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/audit-log")

    response = asyncio.run(request())
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert calls == []


def test_audit_log_route_does_not_modify_the_file(audit_file):
    from app.routes.audit_log import audit_log

    write_entries(audit_file, entry())
    before = (audit_file.read_bytes(), audit_file.stat().st_mtime_ns)
    audit_log()
    assert (audit_file.read_bytes(), audit_file.stat().st_mtime_ns) == before
