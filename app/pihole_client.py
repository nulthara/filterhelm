"""Pi-hole v6 REST API client.

VERIFY BEFORE TRUSTING: the endpoint paths and payload shapes below are the
v6 API as documented, but have not yet been confirmed against a live
instance from inside this codebase. Treat every path constant here as a
hypothesis until a real GET/PATCH round-trip against pihole1 or pihole2
confirms it. Recorded endpoint details are distinct from behaviour confirmed
against a live host.

Auth flow:
  POST   /api/auth            {"password": ...}  -> {"session": {"sid": ..., "csrf": ..., "validity": ...}}
  DELETE /api/auth            (with sid)          -> ends the session

Settings (subset used by the MVP, docs/design.md §5.4):
  GET/PATCH /api/config       full config tree; PATCH is a JSON merge patch —
                               send only the keys that changed, never the
                               whole tree, per the diff-before-write rule.

Relevant config paths within that tree (VERIFY exact nesting/names):
  dns.upstreams              list[str]
  dns.revServers             list[str] of conditional forwarding entries
  dns.queryLogging           bool
  misc.privacylevel          int 0-3

Local DNS:
  PUT/DELETE /api/config/dns/cnameRecords/{value}  # VERIFY — value is "<cname>,<target>[,<ttl>]"

Compatibility and update metadata:
  GET /api/info/version      local/remote Core, Web, FTL, and Docker versions

Blocking control:
  GET  /api/dns/blocking     current blocking state and optional timer
  POST /api/dns/blocking     {"blocking": bool, "timer"?: seconds}

Domain allow/deny (confirmed 2026-09-04 against pihole1: POST add + immediate
dig resolution with no gravity run in between — path shape and no-gravity
behavior both verified):
  GET    /api/domains
  POST   /api/domains/{type}/{kind}
  PUT    /api/domains/{type}/{kind}/{domain}
  DELETE /api/domains/{type}/{kind}/{domain}

Domain lookup (confirmed 2026-09-11 against lab-pihole: shape, regex evaluation,
case-insensitive match, and N-capping all verified read-only):
  GET /api/search/{domain}?partial=<bool>&N=<int>

Query log and 24h statistics (confirmed 2026-09-12 against lab-pihole: shapes,
single-value status filter, wildcard filters, unbounded length and count, pseudo
upstreams all verified read-only):
  GET /api/stats/summary
  GET /api/stats/top_domains?blocked=<bool>&count=<int>
  GET /api/stats/top_clients?blocked=<bool>&count=<int>
  GET /api/stats/upstreams
  GET /api/queries?from=<epoch>&length=<int>&domain=&client_ip=&client_name=&status=

Instance health (confirmed 2026-09-13 against lab-pihole: shapes, kilobyte memory,
millisecond FTL uptime, and sensors roll-up verified read-only):
  GET /api/info/system
  GET /api/info/ftl
  GET /api/info/database
  GET /api/info/sensors

Group management (VERIFY against a live instance — not yet round-tripped from this codebase):
  GET    /api/groups
  POST   /api/groups
  PUT    /api/groups/{name}
  DELETE /api/groups/{name}

Client management (VERIFY against a live instance — not yet round-tripped from this codebase):
  GET    /api/clients
  POST   /api/clients
  PUT    /api/clients/{client}
  DELETE /api/clients/{client}
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import math
from typing import Any
from urllib.parse import quote

import httpx

from app.url_policy import ValidatedDestination, resolve_instance_destination


class PiholeAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _PinnedTransport(httpx.HTTPTransport):
    def __init__(self, destination: ValidatedDestination, *, verify: bool):
        super().__init__(verify=verify)
        self.destination = destination

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        address = self.destination.addresses[0]
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        request.url = request.url.copy_with(
            host=host,
            port=self.destination.port or request.url.port,
        )
        try:
            original_address = ipaddress.ip_address(self.destination.host)
            authority = f"[{original_address.compressed}]" if original_address.version == 6 else original_address.compressed
        except ValueError:
            authority = self.destination.host
        if self.destination.port is not None:
            authority = f"{authority}:{self.destination.port}"
        request.headers["Host"] = authority
        request.extensions = {**request.extensions, "sni_hostname": self.destination.host}
        return super().handle_request(request)


@dataclass
class Session:
    sid: str
    csrf: str


@dataclass(frozen=True)
class BlockingState:
    enabled: bool
    timer: float | None


@dataclass(frozen=True)
class Adlist:
    address: str
    comment: str
    enabled: bool
    groups: tuple[int, ...]
    date_updated: int | None
    number: int | None
    invalid_domains: int | None


@dataclass(frozen=True)
class Domain:
    id: int | None
    domain: str
    type: str
    kind: str
    comment: str
    enabled: bool
    groups: tuple[int, ...]
    date_added: int | None
    date_modified: int | None


@dataclass(frozen=True)
class GravityMatch:
    id: int | None
    domain: str
    address: str
    type: str            # "allow" | "block"
    comment: str
    enabled: bool
    groups: tuple[int, ...]


@dataclass(frozen=True)
class SearchCounts:
    domains_exact: int
    domains_regex: int
    gravity_allow: int
    gravity_block: int
    total: int


@dataclass(frozen=True)
class SearchResult:
    domains: tuple[Domain, ...]
    gravity: tuple[GravityMatch, ...]
    counts: SearchCounts


QUERY_STATUSES = (
    "UNKNOWN", "GRAVITY", "FORWARDED", "CACHE", "REGEX", "DENYLIST",
    "EXTERNAL_BLOCKED_IP", "EXTERNAL_BLOCKED_NULL", "EXTERNAL_BLOCKED_NXRA",
    "GRAVITY_CNAME", "REGEX_CNAME", "DENYLIST_CNAME", "RETRIED", "RETRIED_DNSSEC",
    "IN_PROGRESS", "DBBUSY", "SPECIAL_DOMAIN", "CACHE_STALE", "EXTERNAL_BLOCKED_EDE15",
)

# FTL is_blocked() in src/datastructure.c. DBBUSY is answered with a block.
BLOCKED_STATUSES = frozenset({
    "GRAVITY", "REGEX", "DENYLIST", "EXTERNAL_BLOCKED_IP", "EXTERNAL_BLOCKED_NULL",
    "EXTERNAL_BLOCKED_NXRA", "EXTERNAL_BLOCKED_EDE15", "GRAVITY_CNAME", "REGEX_CNAME",
    "DENYLIST_CNAME", "DBBUSY", "SPECIAL_DOMAIN",
})


@dataclass(frozen=True)
class SystemInfo:
    uptime: float                 # host seconds
    ram_total: int                # kilobytes
    ram_used: int
    ram_available: int
    ram_percent: float
    swap_total: int
    swap_used: int
    swap_percent: float
    cores: int
    cpu_percent: float
    load: tuple[float, float, float]


@dataclass(frozen=True)
class FtlInfo:
    uptime_ms: float
    cpu_percent: float
    mem_percent: float


@dataclass(frozen=True)
class DatabaseInfo:
    size: int                     # bytes
    queries_disk: int
    sqlite_version: str


@dataclass(frozen=True)
class SensorsInfo:
    cpu_temp: float | None
    hot_limit: float | None
    unit: str


@dataclass(frozen=True)
class StatsSummary:
    total: int
    blocked: int
    percent_blocked: float
    unique_domains: int
    forwarded: int
    cached: int
    active_clients: int
    total_clients: int
    gravity_domains: int
    gravity_last_update: int


@dataclass(frozen=True)
class TopDomain:
    domain: str
    count: int


@dataclass(frozen=True)
class TopDomains:
    domains: tuple[TopDomain, ...]
    total_queries: int
    blocked_queries: int


@dataclass(frozen=True)
class TopClient:
    ip: str
    name: str | None
    count: int


@dataclass(frozen=True)
class TopClients:
    clients: tuple[TopClient, ...]
    total_queries: int
    blocked_queries: int


@dataclass(frozen=True)
class Upstream:
    ip: str | None
    name: str | None
    port: int
    count: int
    response: float
    variance: float


@dataclass(frozen=True)
class Upstreams:
    upstreams: tuple[Upstream, ...]
    total_queries: int
    forwarded_queries: int


@dataclass(frozen=True)
class QueryRecord:
    id: int
    time: float
    type: str
    domain: str
    cname: str | None
    status: str | None
    client_ip: str
    client_name: str | None
    upstream: str | None
    reply_type: str | None
    reply_time: float | None


@dataclass(frozen=True)
class QueryPage:
    queries: tuple[QueryRecord, ...]
    records_total: int
    records_filtered: int
    earliest_timestamp: float | None


@dataclass(frozen=True)
class Group:
    id: int | None
    name: str
    comment: str
    enabled: bool
    date_added: int | None
    date_modified: int | None


@dataclass(frozen=True)
class Client:
    id: int | None
    client: str
    comment: str
    groups: tuple[int, ...]
    date_added: int | None
    date_modified: int | None


class PiholeClient:
    """One short-lived client per operation. Always used as a context manager
    so the session is released (`DELETE /api/auth`) even on error — an
    abandoned session is a small leak, not a safety issue, but there's no
    reason to rely on that.
    """

    def __init__(self, base_url: str, password: str, timeout: float = 15.0, verify_ssl: bool = True):
        destination = resolve_instance_destination(base_url)
        self.base_url = destination.url
        self._password = password
        self._client = httpx.Client(
            base_url=destination.url,
            transport=_PinnedTransport(destination, verify=verify_ssl),
            timeout=timeout,
            follow_redirects=False,
        )
        self._session: Session | None = None

    def __enter__(self) -> "PiholeClient":
        self._authenticate()
        return self

    def __exit__(self, *exc) -> None:
        self._logout()
        self._client.close()

    def _authenticate(self) -> None:
        resp = self._client.post("/api/auth", json={"password": self._password})
        if resp.status_code != 200:
            raise PiholeAPIError(f"Pi-hole authentication failed with HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise PiholeAPIError("Pi-hole authentication returned an invalid response") from exc
        sess = body.get("session") or {}
        sid = sess.get("sid")
        if not sid:
            raise PiholeAPIError("Pi-hole authentication response had no session id")
        self._session = Session(sid=sid, csrf=sess.get("csrf", ""))

    def _logout(self) -> None:
        if self._session is None:
            return
        try:
            self._client.delete("/api/auth", headers=self._auth_headers())
        except httpx.HTTPError:
            pass  # best-effort — a failed logout just means the session times out on its own
        finally:
            self._session = None

    def _auth_headers(self) -> dict[str, str]:
        assert self._session is not None
        return {"X-FTL-SID": self._session.sid, "X-FTL-CSRF": self._session.csrf}

    def get_config(self) -> dict[str, Any]:
        resp = self._client.get("/api/config", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/config failed with HTTP {resp.status_code}")
        return resp.json().get("config", resp.json())

    def get_versions(self) -> dict[str, Any]:
        resp = self._client.get("/api/info/version", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/info/version failed with HTTP {resp.status_code}")
        return resp.json().get("version", {})

    @staticmethod
    def _blocking_state(resp: httpx.Response) -> BlockingState:
        try:
            body = resp.json()
        except ValueError as exc:
            raise PiholeAPIError("Pi-hole blocking endpoint returned an invalid response") from exc
        if not isinstance(body, dict):
            raise PiholeAPIError("Pi-hole blocking endpoint returned an invalid response")
        raw = body.get("blocking")
        if isinstance(raw, bool):
            enabled = raw
        elif isinstance(raw, str) and raw in {"enabled", "disabled"}:
            enabled = raw == "enabled"
        else:
            raise PiholeAPIError("Pi-hole blocking response had an invalid state")
        timer = body.get("timer")
        if timer is not None:
            if isinstance(timer, bool) or not isinstance(timer, (int, float)) or not math.isfinite(timer):
                raise PiholeAPIError("Pi-hole blocking response had an invalid timer")
            timer = max(0.0, float(timer))
        return BlockingState(enabled=enabled, timer=timer)

    def get_blocking(self) -> BlockingState:
        resp = self._client.get("/api/dns/blocking", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/dns/blocking failed with HTTP {resp.status_code}")
        return self._blocking_state(resp)

    def set_blocking(self, enabled: bool, timer: int | None = None) -> BlockingState:
        payload: dict[str, bool | int] = {"blocking": enabled}
        if not enabled and timer is not None:
            payload["timer"] = timer
        resp = self._client.post("/api/dns/blocking", json=payload, headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"POST /api/dns/blocking failed with HTTP {resp.status_code}")
        return self._blocking_state(resp)

    def get_adlists(self) -> list[Adlist]:
        resp = self._client.get("/api/lists", params={"type": "block"}, headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/lists failed with HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise PiholeAPIError("Pi-hole lists endpoint returned an invalid response") from exc
        raw_lists = body.get("lists") if isinstance(body, dict) else None
        if not isinstance(raw_lists, list):
            raise PiholeAPIError("Pi-hole lists endpoint returned an invalid response")
        result = []
        for item in raw_lists:
            if not isinstance(item, dict) or not isinstance(item.get("address"), str) or not isinstance(item.get("enabled"), bool):
                raise PiholeAPIError("Pi-hole lists endpoint returned an invalid list")
            groups = item.get("groups", [])
            if not isinstance(groups, list) or not all(isinstance(group, int) and not isinstance(group, bool) for group in groups):
                raise PiholeAPIError("Pi-hole lists endpoint returned invalid groups")
            result.append(
                Adlist(
                    address=item["address"],
                    comment=item.get("comment", "") if isinstance(item.get("comment", ""), str) else "",
                    enabled=item["enabled"],
                    groups=tuple(groups),
                    date_updated=item.get("date_updated") if isinstance(item.get("date_updated"), int) else None,
                    number=item.get("number") if isinstance(item.get("number"), int) else None,
                    invalid_domains=item.get("invalid_domains") if isinstance(item.get("invalid_domains"), int) else None,
                )
            )
        return result

    def add_adlist(self, address: str, comment: str = "") -> None:
        resp = self._client.post(
            "/api/lists",
            params={"type": "block"},
            json={"address": address, "comment": comment, "enabled": True, "groups": [0]},
            headers=self._auth_headers(),
        )
        if resp.status_code not in (200, 201):
            raise PiholeAPIError(f"POST /api/lists failed with HTTP {resp.status_code}")

    def update_adlist(self, adlist: Adlist, *, enabled: bool, groups: list[int]) -> None:
        path = f"/api/lists/{quote(adlist.address, safe='')}"
        resp = self._client.put(
            path,
            params={"type": "block"},
            json={"comment": adlist.comment, "enabled": enabled, "groups": groups},
            headers=self._auth_headers(),
        )
        if resp.status_code != 200:
            raise PiholeAPIError(f"PUT /api/lists failed with HTTP {resp.status_code}")

    def delete_adlist(self, address: str) -> None:
        path = f"/api/lists/{quote(address, safe='')}"
        resp = self._client.delete(path, params={"type": "block"}, headers=self._auth_headers())
        if resp.status_code not in (200, 204):
            raise PiholeAPIError(f"DELETE /api/lists failed with HTTP {resp.status_code}")

    def get_domains(self) -> list[Domain]:
        resp = self._client.get("/api/domains", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/domains failed with HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise PiholeAPIError("Pi-hole domains endpoint returned an invalid response") from exc
        raw_domains = body.get("domains") if isinstance(body, dict) else None
        if not isinstance(raw_domains, list):
            raise PiholeAPIError("Pi-hole domains endpoint returned an invalid response")
        return [self._parse_domain(item) for item in raw_domains]

    @staticmethod
    def _parse_domain(item: Any) -> Domain:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("domain"), str)
            or not isinstance(item.get("type"), str)
            or not isinstance(item.get("kind"), str)
            or not isinstance(item.get("enabled"), bool)
        ):
            raise PiholeAPIError("Pi-hole domains endpoint returned an invalid domain")
        groups = item.get("groups", [])
        if not isinstance(groups, list) or not all(
            isinstance(group, int) and not isinstance(group, bool) for group in groups
        ):
            raise PiholeAPIError("Pi-hole domains endpoint returned invalid groups")
        return Domain(
            id=item.get("id") if isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool) else None,
            domain=item["domain"],
            type=item["type"],
            kind=item["kind"],
            comment=item.get("comment", "") if isinstance(item.get("comment", ""), str) else "",
            enabled=item["enabled"],
            groups=tuple(groups),
            date_added=item.get("date_added") if isinstance(item.get("date_added"), int) and not isinstance(item.get("date_added"), bool) else None,
            date_modified=item.get("date_modified") if isinstance(item.get("date_modified"), int) and not isinstance(item.get("date_modified"), bool) else None,
        )

    @staticmethod
    def _is_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def get_system_info(self) -> SystemInfo:
        resp = self._client.get("/api/info/system", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/info/system failed with HTTP {resp.status_code}")
        error = "Pi-hole system endpoint returned an invalid response"
        try:
            system = resp.json()["system"]
            ram, swap, cpu = system["memory"]["ram"], system["memory"]["swap"], system["cpu"]
            uptime = system["uptime"]
            counters = [ram["total"], ram["used"], ram["available"], swap["total"], swap["used"], cpu["nprocs"]]
            percentages = [ram["%used"], swap["%used"], cpu["%cpu"]]
            load = cpu["load"]["raw"]
            if (not self._is_number(uptime)
                or not all(self._is_int(value) for value in counters)
                or not all(self._is_number(value) for value in percentages)
                or not isinstance(load, list) or len(load) < 3
                or not all(self._is_number(value) for value in load[:3])):
                raise PiholeAPIError(error)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise PiholeAPIError(error) from exc
        return SystemInfo(
            float(uptime), counters[0], counters[1], counters[2], float(percentages[0]),
            counters[3], counters[4], float(percentages[1]), counters[5], float(percentages[2]),
            (float(load[0]), float(load[1]), float(load[2])),
        )

    def get_ftl_info(self) -> FtlInfo:
        resp = self._client.get("/api/info/ftl", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/info/ftl failed with HTTP {resp.status_code}")
        error = "Pi-hole ftl endpoint returned an invalid response"
        try:
            ftl = resp.json()["ftl"]
            uptime, cpu, mem = ftl["uptime"], ftl["%cpu"], ftl["%mem"]
            if not all(self._is_number(value) for value in (uptime, cpu, mem)):
                raise PiholeAPIError(error)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise PiholeAPIError(error) from exc
        return FtlInfo(float(uptime), float(cpu), float(mem))

    def get_database_info(self) -> DatabaseInfo:
        resp = self._client.get("/api/info/database", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/info/database failed with HTTP {resp.status_code}")
        error = "Pi-hole database endpoint returned an invalid response"
        try:
            body = resp.json()
            size, queries, version = body["size"], body["queries_disk"], body["sqlite_version"]
            if not self._is_int(size) or not self._is_int(queries) or not isinstance(version, str):
                raise PiholeAPIError(error)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise PiholeAPIError(error) from exc
        return DatabaseInfo(size, queries, version)

    def get_sensors(self) -> SensorsInfo:
        resp = self._client.get("/api/info/sensors", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/info/sensors failed with HTTP {resp.status_code}")
        error = "Pi-hole sensors endpoint returned an invalid response"
        try:
            sensors = resp.json()["sensors"]
            if not isinstance(sensors, dict):
                raise PiholeAPIError(error)
            temperature, limit, unit = sensors.get("cpu_temp"), sensors.get("hot_limit"), sensors.get("unit")
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise PiholeAPIError(error) from exc
        return SensorsInfo(
            float(temperature) if self._is_number(temperature) and temperature >= 0 else None,
            float(limit) if self._is_number(limit) else None,
            unit if isinstance(unit, str) and unit else "C",
        )

    def get_stats_summary(self) -> StatsSummary:
        resp = self._client.get("/api/stats/summary", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/stats/summary failed with HTTP {resp.status_code}")
        error = "Pi-hole stats endpoint returned an invalid response"
        try:
            body = resp.json()
            queries, clients, gravity = body["queries"], body["clients"], body["gravity"]
            counters = [queries[key] for key in ("total", "blocked", "unique_domains", "forwarded", "cached")]
            counters.extend([clients["active"], clients["total"], gravity["domains_being_blocked"], gravity["last_update"]])
            percent = queries["percent_blocked"]
            if not all(self._is_int(value) for value in counters) or not self._is_number(percent):
                raise PiholeAPIError(error)
        except (ValueError, KeyError, TypeError) as exc:
            raise PiholeAPIError(error) from exc
        return StatsSummary(counters[0], counters[1], percent, *counters[2:])

    def get_top_domains(self, *, blocked: bool, count: int) -> TopDomains:
        resp = self._client.get(
            "/api/stats/top_domains", params={"blocked": "true" if blocked else "false", "count": count},
            headers=self._auth_headers(),
        )
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/stats/top_domains failed with HTTP {resp.status_code}")
        error = "Pi-hole top-domains endpoint returned an invalid response"
        try:
            body = resp.json()
            rows, total, blocked_total = body["domains"], body["total_queries"], body["blocked_queries"]
            if not isinstance(rows, list) or not self._is_int(total) or not self._is_int(blocked_total):
                raise PiholeAPIError(error)
            domains = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("domain"), str) or not self._is_int(row.get("count")):
                    raise PiholeAPIError(error)
                domains.append(TopDomain(row["domain"], row["count"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise PiholeAPIError(error) from exc
        return TopDomains(tuple(domains), total, blocked_total)

    def get_top_clients(self, *, blocked: bool, count: int) -> TopClients:
        resp = self._client.get(
            "/api/stats/top_clients", params={"blocked": "true" if blocked else "false", "count": count},
            headers=self._auth_headers(),
        )
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/stats/top_clients failed with HTTP {resp.status_code}")
        error = "Pi-hole top-clients endpoint returned an invalid response"
        try:
            body = resp.json()
            rows, total, blocked_total = body["clients"], body["total_queries"], body["blocked_queries"]
            if not isinstance(rows, list) or not self._is_int(total) or not self._is_int(blocked_total):
                raise PiholeAPIError(error)
            clients = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("ip"), str) or not self._is_int(row.get("count")):
                    raise PiholeAPIError(error)
                clients.append(TopClient(row["ip"], row.get("name") if isinstance(row.get("name"), str) else None, row["count"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise PiholeAPIError(error) from exc
        return TopClients(tuple(clients), total, blocked_total)

    def get_upstreams(self) -> Upstreams:
        resp = self._client.get("/api/stats/upstreams", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/stats/upstreams failed with HTTP {resp.status_code}")
        error = "Pi-hole upstreams endpoint returned an invalid response"
        try:
            body = resp.json()
            rows, total, forwarded = body["upstreams"], body["total_queries"], body["forwarded_queries"]
            if not isinstance(rows, list) or not self._is_int(total) or not self._is_int(forwarded):
                raise PiholeAPIError(error)
            upstreams = []
            for row in rows:
                if not isinstance(row, dict) or not self._is_int(row.get("port")) or not self._is_int(row.get("count")):
                    raise PiholeAPIError(error)
                stats = row.get("statistics")
                if not isinstance(stats, dict) or not self._is_number(stats.get("response")) or not self._is_number(stats.get("variance")):
                    raise PiholeAPIError(error)
                upstreams.append(Upstream(
                    row.get("ip") if isinstance(row.get("ip"), str) else None,
                    row.get("name") if isinstance(row.get("name"), str) else None,
                    row["port"], row["count"], stats["response"], stats["variance"],
                ))
        except (ValueError, KeyError, TypeError) as exc:
            raise PiholeAPIError(error) from exc
        return Upstreams(tuple(upstreams), total, forwarded)

    def get_queries(
        self, *, start: float | None = None, length: int = 100, domain: str | None = None,
        client_ip: str | None = None, client_name: str | None = None, status: str | None = None,
    ) -> QueryPage:
        params: dict[str, Any] = {"length": length}
        if start is not None:
            params["from"] = int(start)
        for key, value in (("domain", domain), ("client_ip", client_ip), ("client_name", client_name), ("status", status)):
            if value:
                params[key] = value
        resp = self._client.get("/api/queries", params=params, headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/queries failed with HTTP {resp.status_code}")
        error = "Pi-hole queries endpoint returned an invalid response"
        try:
            body = resp.json()
        except ValueError as exc:
            raise PiholeAPIError(error) from exc
        if (not isinstance(body, dict) or not isinstance(body.get("queries"), list)
                or not self._is_int(body.get("recordsTotal")) or not self._is_int(body.get("recordsFiltered"))):
            raise PiholeAPIError(error)
        records = []
        for row in body["queries"]:
            if (not isinstance(row, dict) or not self._is_int(row.get("id"))
                    or not self._is_number(row.get("time")) or not isinstance(row.get("type"), str)
                    or not isinstance(row.get("domain"), str) or not isinstance(row.get("client"), dict)
                    or not isinstance(row["client"].get("ip"), str)):
                raise PiholeAPIError("Pi-hole queries endpoint returned an invalid query record")
            reply = row.get("reply")
            reply = reply if isinstance(reply, dict) else {}
            records.append(QueryRecord(
                row["id"], row["time"], row["type"], row["domain"],
                row.get("cname") if isinstance(row.get("cname"), str) else None,
                row.get("status") if isinstance(row.get("status"), str) else None,
                row["client"]["ip"],
                row["client"].get("name") if isinstance(row["client"].get("name"), str) else None,
                row.get("upstream") if isinstance(row.get("upstream"), str) else None,
                reply.get("type") if isinstance(reply.get("type"), str) else None,
                reply.get("time") if self._is_number(reply.get("time")) else None,
            ))
        earliest = body.get("earliest_timestamp")
        return QueryPage(tuple(records), body["recordsTotal"], body["recordsFiltered"],
                         earliest if self._is_number(earliest) else None)

    def search_domain(self, term: str, *, partial: bool = False, limit: int = 20) -> SearchResult:
        path = f"/api/search/{quote(term, safe='')}"
        resp = self._client.get(
            path,
            params={"partial": "true" if partial else "false", "N": limit},
            headers=self._auth_headers(),
        )
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/search failed with HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise PiholeAPIError("Pi-hole search endpoint returned an invalid response") from exc
        search = body.get("search") if isinstance(body, dict) else None
        if (
            not isinstance(search, dict)
            or not isinstance(search.get("domains"), list)
            or not isinstance(search.get("gravity"), list)
            or not isinstance(search.get("results"), dict)
        ):
            raise PiholeAPIError("Pi-hole search endpoint returned an invalid response")
        domains = tuple(self._parse_domain(item) for item in search["domains"])
        gravity = []
        for item in search["gravity"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("domain"), str)
                or not isinstance(item.get("address"), str)
                or not isinstance(item.get("type"), str)
                or not isinstance(item.get("enabled"), bool)
                or not isinstance(item.get("groups"), list)
                or not all(isinstance(group, int) and not isinstance(group, bool) for group in item["groups"])
            ):
                raise PiholeAPIError("Pi-hole search endpoint returned an invalid gravity match")
            gravity.append(GravityMatch(
                id=item.get("id") if isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool) else None,
                domain=item["domain"],
                address=item["address"],
                type=item["type"],
                comment=item.get("comment", "") if isinstance(item.get("comment", ""), str) else "",
                enabled=item["enabled"],
                groups=tuple(item["groups"]),
            ))
        results = search["results"]
        domain_counts = results.get("domains")
        gravity_counts = results.get("gravity")
        if not isinstance(domain_counts, dict) or not isinstance(gravity_counts, dict):
            raise PiholeAPIError("Pi-hole search endpoint returned invalid result counts")
        counts = (
            domain_counts.get("exact"), domain_counts.get("regex"),
            gravity_counts.get("allow"), gravity_counts.get("block"), results.get("total"),
        )
        if not all(isinstance(count, int) and not isinstance(count, bool) for count in counts):
            raise PiholeAPIError("Pi-hole search endpoint returned invalid result counts")
        return SearchResult(domains, tuple(gravity), SearchCounts(*counts))

    def add_domain(self, domain: str, type: str, kind: str, comment: str = "") -> None:
        path = f"/api/domains/{quote(type, safe='')}/{quote(kind, safe='')}"
        resp = self._client.post(
            path,
            json={"domain": domain, "comment": comment, "enabled": True, "groups": [0]},
            headers=self._auth_headers(),
        )
        if resp.status_code not in (200, 201):
            raise PiholeAPIError(f"POST /api/domains failed with HTTP {resp.status_code}")

    def update_domain(self, item: Domain, *, enabled: bool, groups: list[int]) -> None:
        path = f"/api/domains/{quote(item.type, safe='')}/{quote(item.kind, safe='')}/{quote(item.domain, safe='')}"
        resp = self._client.put(
            path,
            json={"comment": item.comment, "enabled": enabled, "groups": groups},
            headers=self._auth_headers(),
        )
        if resp.status_code != 200:
            raise PiholeAPIError(f"PUT /api/domains failed with HTTP {resp.status_code}")

    def delete_domain(self, domain: str, type: str, kind: str) -> None:
        path = f"/api/domains/{quote(type, safe='')}/{quote(kind, safe='')}/{quote(domain, safe='')}"
        resp = self._client.delete(path, headers=self._auth_headers())
        if resp.status_code not in (200, 204):
            raise PiholeAPIError(f"DELETE /api/domains failed with HTTP {resp.status_code}")

    def get_groups(self) -> list[Group]:
        resp = self._client.get("/api/groups", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/groups failed with HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise PiholeAPIError("Pi-hole groups endpoint returned an invalid response") from exc
        raw_groups = body.get("groups") if isinstance(body, dict) else None
        if not isinstance(raw_groups, list):
            raise PiholeAPIError("Pi-hole groups endpoint returned an invalid response")
        result = []
        for item in raw_groups:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("enabled"), bool):
                raise PiholeAPIError("Pi-hole groups endpoint returned an invalid group")
            result.append(Group(
                id=item.get("id") if isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool) else None,
                name=item["name"],
                comment=item.get("comment", "") if isinstance(item.get("comment", ""), str) else "",
                enabled=item["enabled"],
                date_added=item.get("date_added") if isinstance(item.get("date_added"), int) and not isinstance(item.get("date_added"), bool) else None,
                date_modified=item.get("date_modified") if isinstance(item.get("date_modified"), int) and not isinstance(item.get("date_modified"), bool) else None,
            ))
        return result

    def add_group(self, name: str, comment: str = "") -> None:
        resp = self._client.post("/api/groups", json={"name": name, "comment": comment, "enabled": True}, headers=self._auth_headers())
        if resp.status_code not in (200, 201):
            raise PiholeAPIError(f"POST /api/groups failed with HTTP {resp.status_code}")

    def update_group(self, group: Group, *, enabled: bool) -> None:
        path = f"/api/groups/{quote(group.name, safe='')}"
        resp = self._client.put(path, json={"name": group.name, "comment": group.comment, "enabled": enabled}, headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"PUT /api/groups failed with HTTP {resp.status_code}")

    def delete_group(self, name: str) -> None:
        resp = self._client.delete(f"/api/groups/{quote(name, safe='')}", headers=self._auth_headers())
        if resp.status_code not in (200, 204):
            raise PiholeAPIError(f"DELETE /api/groups failed with HTTP {resp.status_code}")

    def get_clients(self) -> list[Client]:
        resp = self._client.get("/api/clients", headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"GET /api/clients failed with HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise PiholeAPIError("Pi-hole clients endpoint returned an invalid response") from exc
        raw_clients = body.get("clients") if isinstance(body, dict) else None
        if not isinstance(raw_clients, list):
            raise PiholeAPIError("Pi-hole clients endpoint returned an invalid response")
        result = []
        for item in raw_clients:
            if not isinstance(item, dict) or not isinstance(item.get("client"), str):
                raise PiholeAPIError("Pi-hole clients endpoint returned an invalid client")
            groups = item.get("groups", [])
            if not isinstance(groups, list) or not all(isinstance(group, int) and not isinstance(group, bool) for group in groups):
                raise PiholeAPIError("Pi-hole clients endpoint returned invalid groups")
            result.append(Client(
                id=item.get("id") if isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool) else None,
                client=item["client"],
                comment=item.get("comment", "") if isinstance(item.get("comment", ""), str) else "",
                groups=tuple(groups),
                date_added=item.get("date_added") if isinstance(item.get("date_added"), int) and not isinstance(item.get("date_added"), bool) else None,
                date_modified=item.get("date_modified") if isinstance(item.get("date_modified"), int) and not isinstance(item.get("date_modified"), bool) else None,
            ))
        return result

    def add_client(self, client: str, comment: str = "") -> None:
        resp = self._client.post("/api/clients", json={"client": client, "comment": comment, "groups": [0]}, headers=self._auth_headers())
        if resp.status_code not in (200, 201):
            raise PiholeAPIError(f"POST /api/clients failed with HTTP {resp.status_code}")

    def update_client(self, item: Client, *, groups: list[int]) -> None:
        path = f"/api/clients/{quote(item.client, safe='')}"
        resp = self._client.put(path, json={"comment": item.comment, "groups": groups}, headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"PUT /api/clients failed with HTTP {resp.status_code}")

    def delete_client(self, client: str) -> None:
        resp = self._client.delete(f"/api/clients/{quote(client, safe='')}", headers=self._auth_headers())
        if resp.status_code not in (200, 204):
            raise PiholeAPIError(f"DELETE /api/clients failed with HTTP {resp.status_code}")

    def update_gravity(self) -> None:
        # Gravity may run for minutes and streams progress output. Consume and
        # discard it so completion is observed without buffering the output or
        # exposing it through this application's error responses.
        with self._client.stream(
            "POST", "/api/action/gravity", headers=self._auth_headers(), timeout=300.0
        ) as resp:
            if resp.status_code != 200:
                raise PiholeAPIError(f"POST /api/action/gravity failed with HTTP {resp.status_code}")
            for _chunk in resp.iter_bytes():
                pass

    def patch_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        """`changes` is a JSON merge patch — only the keys being changed,
        nested to match the real config tree (e.g. {"dns": {"queryLogging": False}}).
        Never pass a full config tree here; that would defeat the point of a
        merge patch and risks clobbering fields this app doesn't know about.
        """
        resp = self._client.patch("/api/config", json={"config": changes}, headers=self._auth_headers())
        if resp.status_code != 200:
            raise PiholeAPIError(f"PATCH /api/config failed with HTTP {resp.status_code}")
        return resp.json()

    def add_dns_host(self, record: str) -> None:
        """Append one dns.hosts entry without replacing the full array."""
        path = f"/api/config/dns/hosts/{quote(record, safe='')}"
        resp = self._client.put(path, headers=self._auth_headers())
        if resp.status_code not in (200, 201, 204):
            raise PiholeAPIError(f"PUT dns.hosts entry failed with HTTP {resp.status_code}")

    def delete_dns_host(self, record: str) -> None:
        """Remove one exact dns.hosts entry without replacing the full array."""
        path = f"/api/config/dns/hosts/{quote(record, safe='')}"
        resp = self._client.delete(path, headers=self._auth_headers())
        if resp.status_code not in (200, 204):
            raise PiholeAPIError(f"DELETE dns.hosts entry failed with HTTP {resp.status_code}")

    def add_dns_cname(self, record: str) -> None:
        """Append one dns.cnameRecords entry without replacing the full array."""
        path = f"/api/config/dns/cnameRecords/{quote(record, safe='')}"
        resp = self._client.put(path, headers=self._auth_headers())
        if resp.status_code not in (200, 201, 204):
            raise PiholeAPIError(
                f"PUT dns.cnameRecords entry failed with HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

    def delete_dns_cname(self, record: str) -> None:
        """Remove one exact dns.cnameRecords entry without replacing the full array."""
        path = f"/api/config/dns/cnameRecords/{quote(record, safe='')}"
        resp = self._client.delete(path, headers=self._auth_headers())
        if resp.status_code not in (200, 204):
            raise PiholeAPIError(
                f"DELETE dns.cnameRecords entry failed with HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

    def test_connection(self) -> None:
        """Raises PiholeAPIError on failure. Used by the "test connection"
        button in instance setup — the same auth call the app uses at
        runtime, so a pass here means the real thing will work too.
        """
        self.get_config()
