# 0014 — Cross-instance query log and 24-hour stats

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0014-query-log
Author: Claude (architect)
Date: 2026-09-12

## Problem

Answering "what got blocked in the last hour, and for which client?" or "which client is
the noisiest today?" means opening each Pi-hole's own dashboard in turn, because this app
never reads the query log or the 24-hour statistics. Pi-hole v6 exposes the log at
`GET /api/queries` and the counters at `GET /api/stats/summary`, `/api/stats/top_domains`,
`/api/stats/top_clients`, and `/api/stats/upstreams`, and `app/pihole_client.py` wraps none
of them. A read-only Queries page that fans all of them out across every instance, merges
the log newest-first, and shows each instance's top lists and upstream breakdown is the
whole potential-features.md §2 dashboard item. It has no mutation surface and follows the
same fan-out shape as Status and Lookup.

## Scope

In:
- `PiholeClient.get_stats_summary`, `get_top_domains`, `get_top_clients`, `get_upstreams`
  wrapping the four `/api/stats/*` reads, and `PiholeClient.get_queries` wrapping
  `GET /api/queries` with `from`, `length`, `domain`, `client_ip`, `client_name`, and
  `status`.
- A new read-only route `GET /api/query-log` that, per instance and inside one Pi-hole
  session, reads the summary, both top-domain lists, both top-client lists, the upstreams,
  and the filtered query log, then returns per-instance stats, a merged newest-first row
  list tagged by instance, and a combined counter.
- A "Blocked (any reason)" log filter implemented server-side as one FTL call per blocked
  status, because FTL accepts exactly one `status` value per request.
- A new **Queries** nav page: filter bar (window, status, domain, client, per-instance
  limit), one summary card per instance plus a combined card, and tabs for the merged
  query log, top domains, top clients, and upstreams. Loads once on open and on Refresh or
  Apply. No timer.
- Docs: design.md §5.9, README compatibility paragraph, potential-features.md shipped
  list, the `pihole_client.py` endpoint inventory.

Out:
- Cursor or offset paging across instances. The page caps rows per instance and tells the
  user how many matched; narrowing the window or filter is the answer.
- Long-term history (`disk=true`, `/api/stats/database/*`). The log window tops out at 24
  hours, which is what FTL keeps in memory, and every stats read is FTL's fixed 24-hour
  in-memory view.
- Combined top lists across instances. Summing two instances' top-10 lists misses every
  domain that sits outside one instance's top 10, so the page shows top lists per
  instance only.
- A user-selectable top-list size. The route asks FTL for a fixed 10 per list.
- Auto-refresh or live tailing. One fan-out per user action.
- Any action from a row (add to allowlist, look up the domain). Lookup and Domains stay
  where they are.
- Frontend tests. `frontend/` still has no test runner (potential-features.md §5); the
  merge, blocked expansion, filter classification, and serializers therefore live in the
  backend where pytest covers them.

## Approach

**One endpoint, one session per instance, every read inside it.** Two concurrent
authenticated calls to one instance can race at login and 401, so the route opens one
`PiholeClient` per instance and runs the reads sequentially inside it: summary, top
domains (permitted, blocked), top clients (permitted, blocked), upstreams, then the log.
The fan-out is across instances only, exactly like `lookup.py`. The frontend makes one
request per refresh. Changing a log filter re-reads the stats too; that costs six
sub-millisecond in-memory reads per instance and keeps the contract to one response with
one loading state.

**Live-confirmed on lab-pihole, 2026-09-12 (read-only GETs, nothing written):**

- `GET /api/stats/summary` takes no parameters and returns `{"queries": {"total",
  "blocked", "percent_blocked", "unique_domains", "forwarded", "cached", "frequency",
  "types": {...}, "status": {...}, "replies": {...}}, "clients": {"active", "total"},
  "gravity": {"domains_being_blocked", "last_update"}, "took"}`. Counters cover the
  in-memory 24-hour window and cannot be scoped by time.
- `GET /api/stats/top_domains?blocked=<bool>&count=<int>` returns `{"domains": [{"domain",
  "count"}], "total_queries", "blocked_queries", "took"}`, ranked by count descending.
  `blocked=true` ranks blocked queries only. The default `count` is 10; `count=0` and
  `count=-1` each returned one row and `count=abc` fell back to 10, so the route always
  sends an explicit positive count.
- `GET /api/stats/top_clients?blocked=<bool>&count=<int>` has the same shape with
  `"clients": [{"ip", "name", "count"}]`. `name` may be `null` per the spec.
- `GET /api/stats/upstreams` takes no parameters and returns `{"upstreams": [{"ip",
  "name", "port", "count", "statistics": {"response", "variance"}}],
  "forwarded_queries", "total_queries", "took"}`. It includes two pseudo-upstreams,
  `blocklist` and `cache`, with `port: -1` and zero statistics; real upstreams carry
  `port: 53` and response times in seconds (`0.0207` for `1.0.0.2`). The spec marks `ip`
  and `name` nullable.
- `GET /api/queries` returns `{"queries": [...], "cursor", "recordsTotal",
  "recordsFiltered", "draw", "earliest_timestamp", "earliest_timestamp_disk", "took"}`.
  Each item has `id`, `time` (float seconds), `type`, `status`, `dnssec`, `domain`,
  `upstream` (string or `null`), `reply: {type, time}`, `client: {ip, name}` (`name`
  may be `null`), `list_id`, `ede: {code, text}`, `cname` (`null` unless a CNAME was
  blocked). `recordsFiltered` honours every filter including `from`; `recordsTotal` is the
  unfiltered in-memory count. `earliest_timestamp` is the same for every filter, so one
  read of it per instance is enough.
- `status` accepts exactly one value. `status=GRAVITY,REGEX` and `status=BOGUS` both return
  400 `Requested status is invalid`. Matching is case-insensitive (`cache_stale` works).
- `domain`, `client_ip`, and `client_name` accept `*` wildcards: `*arpa` matched 155 rows,
  `*.arpa` 153, the exact `1.1.1.in-addr.arpa` 17; `client_name=pi.hole` and
  `client_ip=::` each matched 97. Domain matching is case-insensitive (`*ARPA` = `*arpa`).
  A domain containing a space silently matched everything, so the route rejects it.
- `length=-1` returned every row and `length=abc` fell back to 100. FTL does not bound
  `length`, so the route must. `start` offsets work but this plan does not use them.
- `from`/`until` are epoch seconds compared against FTL's own timestamps.

**Blocked means what FTL says it means.** `BLOCKED_STATUSES` is FTL's `is_blocked()` set
from `src/datastructure.c` (master, read 2026-09-12): GRAVITY, REGEX, DENYLIST,
EXTERNAL_BLOCKED_IP, EXTERNAL_BLOCKED_NULL, EXTERNAL_BLOCKED_NXRA, EXTERNAL_BLOCKED_EDE15,
GRAVITY_CNAME, REGEX_CNAME, DENYLIST_CNAME, DBBUSY, SPECIAL_DOMAIN. DBBUSY belongs there
because FTL answers those queries with a block while the database is busy. Using this set
keeps the page's "Blocked" filter, the summary's `blocked` counter, and the `blocked=true`
top lists in agreement. A test pins the literal set so an edit cannot drift it silently.

**"Blocked (any reason)" fans in over statuses, server-side.** Because FTL takes one
status per request, the worker issues one `get_queries` per blocked status inside the same
session, sums `recordsFiltered` (statuses partition the log, so the sum is exact), merges
the rows newest-first, and caps at `length`. That is 12 cheap in-memory reads per
instance; the probe measured each at well under a millisecond on FTL's side. The
alternative, one unfiltered read filtered in this app, cannot promise the cap covers the
window and was rejected. `/api/stats/recent_blocked` was rejected because it returns
domains only, with no client or time.

**Merge and classify in the backend.** The merged newest-first ordering, the blocked
expansion, the client-filter classification, the combined counter, and the stats
serializers are all pure logic, and the frontend has no test runner. They live in
`query_log.py` under pytest, and the page renders what it is given.

**One client box, two FTL filters.** FTL splits client filtering into `client_ip` and
`client_name`, and it ANDs filters, so sending the same string to both matches nothing.
The route classifies the typed value as an IP pattern when every character is a hex digit,
`.`, `:`, or `*`, and the string contains at least one `.` or `:`; otherwise it is a name
pattern. `192.168.1.*`, `fe80::1`, and `::` go to `client_ip`; `nas`, `nas.lan`, and
`*phone*` go to `client_name`. A hostname made only of hex letters and dots (`cafe.dead`)
would misroute; that edge is accepted and noted in the page's placeholder text.

**The stats are the 24-hour picture, not the window's.** Every `/api/stats/*` read is
fixed to FTL's in-memory day. The summary cards and the three stats tabs say "last 24
hours" and do not change with the window selector, which scopes the log only. Deriving
window-scoped stats from `/api/stats/database/*` was rejected for v1 because it hits the
on-disk database on every refresh and this page should stay cheap to open.

**Top lists are per instance, fixed at 10, both flavours.** Each instance gets permitted
and blocked top domains and top clients, because "what is this client hammering" and
"what is being blocked most" are different questions and FTL answers them with one flag.
`TOP_COUNT = 10` is sent explicitly because FTL's handling of `count` at the edges is
odd (above). Shares are computed in the browser from the summary: a permitted row's share
is against `total - blocked`, a blocked row's against `blocked`, and an upstream's against
`total`, which is how Pi-hole's own dashboard presents them.

**Upstream pseudo-entries stay.** FTL reports `cache` and `blocklist` as destinations
with `port: -1`. They are kept in the contract and the table because "how much never left
the box" is part of the upstream picture, and the page renders them without an address
or a response time.

**Bounds are the app's job.** `window` is 60 to 86400 seconds (24 hours is the in-memory
horizon), `length` is 1 to 500 per instance. The route computes `since = now - window` on
the app server; FTL compares it against its own clock, and a skewed Pi-hole clock shifts
the window by the skew. Each instance's `earliest_timestamp` is returned so the page can
say when an instance's in-memory history starts if the window reaches past it.

**Privacy levels pass through.** FTL redacts domains or clients according to
`misc.privacylevel`; the page shows whatever FTL returns and adds nothing.

Rejected alternatives:

- **Two routes, one for the log and one for the stats.** Rejected: two sessions per
  instance per open, the concurrent-login race is real so the browser would have to chain
  them, and two loading states for one page. The six extra reads per apply are cheaper
  than that.
- **Put the route in `app/routes/lookup.py`.** Rejected: different endpoints, different
  concern; AGENTS.md's one-module-per-concern rule points to a new `query_log.py`.
- **Name the route `/api/queries`.** Rejected: it would shadow the Pi-hole path name in
  every conversation and log line about it. `/api/query-log` is unambiguous.
- **Combined top lists.** Rejected for the truncation reason in Scope.

## Files

- `app/pihole_client.py` — `QUERY_STATUSES`, `BLOCKED_STATUSES`; `StatsSummary`,
  `TopDomain`, `TopDomains`, `TopClient`, `TopClients`, `Upstream`, `Upstreams`,
  `QueryRecord`, `QueryPage` dataclasses; `get_stats_summary`, `get_top_domains`,
  `get_top_clients`, `get_upstreams`, `get_queries`; docstring inventory gains the five
  confirmed endpoints.
- `app/routes/query_log.py` — new; `QueryLogQuery` model, `_client_filter`, serializers,
  `_query_log_instance`, `_combined`, `GET /api/query-log`. Copies `_fanout` / `_client`
  / `credentials` / `router` from `lookup.py`.
- `app/main.py` — `app.include_router(query_log.router)`.
- `frontend/src/api.ts` — `QuerySummary`, `TopDomain`, `TopClient`, `UpstreamStat`,
  `QueryLogInstance`, `QueryLogRow`, `QueryLogCombined`, `QueryLogFilters`,
  `QueryLogResponse` types and `api.queryLog`.
- `frontend/src/pages/QueriesPage.tsx` — new; filter bar, summary cards, tabs for the
  log, top domains, top clients, upstreams.
- `frontend/src/App.tsx` — `queries` nav entry with `HistoryOutlined`, rendered after
  Status.
- `frontend/src/styles.css` — `.query-filters`, `.query-summary`, `.query-top-pair`
  layout classes.
- `tests/test_pihole_client.py` — summary, top lists, upstreams, and queries
  request/parse tests.
- `tests/test_query_log.py` — new; route fan-out, session read order, merge, blocked
  expansion, validation, classification, serializers, combined counter tests.
- `docs/design.md` — new §5.9 "Query log and statistics".
- `README.md` — a paragraph in "Pi-hole compatibility" for the Queries page.
- `docs/potential-features.md` — move the §2 dashboard bullet into the shipped list;
  rewrite "Suggested next pick".
- `app/static/ui/` — rebuilt bundle (invariant #8).

## Steps

1. **`app/pihole_client.py` — constants and dataclasses.** Next to `SearchResult`, add:

   ```python
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
   ```

   Add two private helpers the readers below share, so "non-bool int" and "non-bool
   number" are written once:

   ```python
   @staticmethod
   def _is_int(value: Any) -> bool:
       return isinstance(value, int) and not isinstance(value, bool)

   @staticmethod
   def _is_number(value: Any) -> bool:
       return isinstance(value, (int, float)) and not isinstance(value, bool)
   ```

2. **`app/pihole_client.py` — `get_stats_summary`.**

   ```python
   def get_stats_summary(self) -> StatsSummary:
       resp = self._client.get("/api/stats/summary", headers=self._auth_headers())
       if resp.status_code != 200:
           raise PiholeAPIError(f"GET /api/stats/summary failed with HTTP {resp.status_code}")
       ...
   ```

   Parse `body["queries"]`, `body["clients"]`, `body["gravity"]`. Require
   `queries.total`, `queries.blocked`, `queries.unique_domains`, `queries.forwarded`,
   `queries.cached`, `clients.active`, `clients.total`, `gravity.domains_being_blocked`,
   `gravity.last_update` to pass `_is_int` and `queries.percent_blocked` to pass
   `_is_number`, else `PiholeAPIError("Pi-hole stats endpoint returned an invalid
   response")`. Nothing defaults to `0`, matching `search_domain`'s treatment of counts.

3. **`app/pihole_client.py` — `get_top_domains`, `get_top_clients`, `get_upstreams`.**

   ```python
   def get_top_domains(self, *, blocked: bool, count: int) -> TopDomains:
       resp = self._client.get(
           "/api/stats/top_domains",
           params={"blocked": "true" if blocked else "false", "count": count},
           headers=self._auth_headers(),
       )
       if resp.status_code != 200:
           raise PiholeAPIError(f"GET /api/stats/top_domains failed with HTTP {resp.status_code}")
       ...

   def get_top_clients(self, *, blocked: bool, count: int) -> TopClients:
       ...  # same shape against /api/stats/top_clients

   def get_upstreams(self) -> Upstreams:
       resp = self._client.get("/api/stats/upstreams", headers=self._auth_headers())
       ...
   ```

   `blocked` is always sent explicitly, encoded the way `search_domain` encodes `partial`.
   `get_top_domains` requires a list `domains` and `_is_int` `total_queries` and
   `blocked_queries`; each item requires `domain: str` and `_is_int` `count`, else
   `PiholeAPIError("Pi-hole top-domains endpoint returned an invalid response")`.
   `get_top_clients` requires a list `clients` and the same two totals; each item requires
   `ip: str` and `_is_int` `count`, and keeps `name` when it is a `str`, else `None`.
   Error message `"Pi-hole top-clients endpoint returned an invalid response"`.
   `get_upstreams` requires a list `upstreams` and `_is_int` `total_queries` and
   `forwarded_queries`; each item requires `_is_int` `port` and `count` and a dict
   `statistics` whose `response` and `variance` pass `_is_number`; `ip` and `name` are kept
   when `str`, else `None`. Error message `"Pi-hole upstreams endpoint returned an invalid
   response"`. Pseudo-upstreams with `port == -1` are returned as-is; the route and the
   page decide how to show them.

4. **`app/pihole_client.py` — `get_queries`.**

   ```python
   def get_queries(
       self, *, start: float | None = None, length: int = 100, domain: str | None = None,
       client_ip: str | None = None, client_name: str | None = None, status: str | None = None,
   ) -> QueryPage:
       params: dict[str, Any] = {"length": length}
       if start is not None: params["from"] = int(start)
       if domain: params["domain"] = domain
       if client_ip: params["client_ip"] = client_ip
       if client_name: params["client_name"] = client_name
       if status: params["status"] = status
       resp = self._client.get("/api/queries", params=params, headers=self._auth_headers())
       if resp.status_code != 200:
           raise PiholeAPIError(f"GET /api/queries failed with HTTP {resp.status_code}")
       ...
   ```

   Only provided filters are sent; an absent key is how FTL knows not to filter. Require
   `body` to be a dict with a list `queries` and `_is_int` `recordsTotal` and
   `recordsFiltered`, else
   `PiholeAPIError("Pi-hole queries endpoint returned an invalid response")`.
   `earliest_timestamp` is kept when `_is_number` and becomes `None` otherwise. Each item
   requires `_is_int` `id`, `_is_number` `time`, `type: str`, `domain: str`, and
   `client: dict` with `ip: str`, else `PiholeAPIError("Pi-hole queries endpoint returned
   an invalid query record")`. `cname`, `status`, `upstream`, `client.name`, `reply.type`
   are kept when they are `str` and become `None` otherwise; `reply.time` is kept when
   `_is_number` and becomes `None` otherwise. Identity fields are strict, descriptive
   fields are lenient, the same split `search_domain` uses. The docstring inventory gets:

   ```
   Query log and 24h statistics (confirmed 2026-09-12 against lab-pihole: shapes,
   single-value status filter, wildcard filters, unbounded length and count, pseudo
   upstreams all verified read-only):
     GET /api/stats/summary
     GET /api/stats/top_domains?blocked=<bool>&count=<int>
     GET /api/stats/top_clients?blocked=<bool>&count=<int>
     GET /api/stats/upstreams
     GET /api/queries?from=<epoch>&length=<int>&domain=&client_ip=&client_name=&status=
   ```

5. **`app/routes/query_log.py` — module and query model.** New module, docstring
   `"""Read-only query log and 24h statistics across every instance."""`. Copy
   `_fanout`, `_client`, `credentials`, `router = APIRouter(prefix="/api")` from
   `lookup.py` verbatim. No audit import; nothing here writes. Constants:

   ```python
   WINDOW_MIN, WINDOW_MAX, WINDOW_DEFAULT = 60, 86_400, 3_600
   LENGTH_MIN, LENGTH_MAX, LENGTH_DEFAULT = 1, 500, 100
   TOP_COUNT = 10
   _IP_CHARS = frozenset("0123456789abcdefABCDEF.:*")


   def _now() -> float:
       return time.time()
   ```

   ```python
   class QueryLogQuery(BaseModel):
       window: int = Field(default=WINDOW_DEFAULT, ge=WINDOW_MIN, le=WINDOW_MAX)
       length: int = Field(default=LENGTH_DEFAULT, ge=LENGTH_MIN, le=LENGTH_MAX)
       domain: str = Field(default="", max_length=253)
       client: str = Field(default="", max_length=253)
       status: str = Field(default="", max_length=32)

       @field_validator("domain")
       @classmethod
       def validate_domain(cls, value: str) -> str:
           term = value.strip().lower()
           if any(ch.isspace() for ch in term) or "/" in term:
               raise ValueError("A domain filter cannot contain spaces or slashes")
           return term

       @field_validator("client")
       @classmethod
       def validate_client(cls, value: str) -> str:
           term = value.strip()
           if any(ch.isspace() for ch in term) or "/" in term:
               raise ValueError("A client filter cannot contain spaces or slashes")
           return term

       @field_validator("status")
       @classmethod
       def validate_status(cls, value: str) -> str:
           term = value.strip().upper()
           if term in ("", "BLOCKED"):
               return term.lower()
           if term not in QUERY_STATUSES:
               raise ValueError("Unknown query status")
           return term
   ```

   The echoed `status` is therefore `""`, `"blocked"`, or an uppercase FTL status. Empty
   `domain` and `client` mean no filter. Wildcards are passed through untouched.

6. **`app/routes/query_log.py` — client classification and serializers.**

   ```python
   def _client_filter(value: str) -> tuple[str | None, str | None]:
       """(client_ip, client_name) for FTL. Exactly one is set when value is non-empty."""
       if not value:
           return None, None
       looks_like_ip = set(value) <= _IP_CHARS and ("." in value or ":" in value)
       return (value, None) if looks_like_ip else (None, value)


   def _summary(item: StatsSummary) -> dict[str, Any]:
       return {"total": item.total, "blocked": item.blocked, "percent_blocked": item.percent_blocked,
               "unique_domains": item.unique_domains, "forwarded": item.forwarded, "cached": item.cached,
               "active_clients": item.active_clients, "total_clients": item.total_clients,
               "gravity_domains": item.gravity_domains, "gravity_last_update": item.gravity_last_update}


   def _top_domains(item: TopDomains) -> list[dict[str, Any]]:
       return [{"domain": d.domain, "count": d.count} for d in item.domains]


   def _top_clients(item: TopClients) -> list[dict[str, Any]]:
       return [{"ip": c.ip, "name": c.name, "count": c.count} for c in item.clients]


   def _upstreams(item: Upstreams) -> list[dict[str, Any]]:
       return [{"ip": u.ip, "name": u.name, "port": u.port, "count": u.count,
                "response": u.response, "variance": u.variance} for u in item.upstreams]


   def _row(instance: str, item: QueryRecord) -> dict[str, Any]:
       return {"instance": instance, "id": item.id, "time": item.time, "type": item.type,
               "domain": item.domain, "cname": item.cname, "status": item.status,
               "blocked": item.status in BLOCKED_STATUSES,
               "client_ip": item.client_ip, "client_name": item.client_name,
               "upstream": item.upstream, "reply_type": item.reply_type, "reply_time": item.reply_time}
   ```

   The top-list and upstream totals (`total_queries`, `blocked_queries`,
   `forwarded_queries`) are not serialized; the summary already carries them.

7. **`app/routes/query_log.py` — per-instance worker.**

   ```python
   def _query_log_instance(
       instance: Instance, query: QueryLogQuery, since: float,
   ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], str | None]:
       client_ip, client_name = _client_filter(query.client)
       statuses = sorted(BLOCKED_STATUSES) if query.status == "blocked" else [query.status or None]
       try:
           with _client(instance) as client:
               summary = client.get_stats_summary()
               top_domains = {flag: client.get_top_domains(blocked=flag, count=TOP_COUNT) for flag in (False, True)}
               top_clients = {flag: client.get_top_clients(blocked=flag, count=TOP_COUNT) for flag in (False, True)}
               upstreams = client.get_upstreams()
               pages = [
                   client.get_queries(start=since, length=query.length, domain=query.domain or None,
                                      client_ip=client_ip, client_name=client_name, status=status)
                   for status in statuses
               ]
       except HTTPException as exc:
           return instance.name, None, [], str(exc.detail)
       except PiholeAPIError as exc:
           return instance.name, None, [], str(exc)
       except Exception:  # noqa: BLE001 - transport details are intentionally hidden
           return instance.name, None, [], "Unable to reach Pi-hole instance"
       records = sorted((r for page in pages for r in page.queries), key=lambda r: (-r.time, -r.id))[:query.length]
       rows = [_row(instance.name, r) for r in records]
       return instance.name, {
           "summary": _summary(summary),
           "top_domains": {"permitted": _top_domains(top_domains[False]), "blocked": _top_domains(top_domains[True])},
           "top_clients": {"permitted": _top_clients(top_clients[False]), "blocked": _top_clients(top_clients[True])},
           "upstreams": _upstreams(upstreams),
           "records_filtered": sum(page.records_filtered for page in pages),
           "returned": len(rows),
           "earliest_timestamp": pages[0].earliest_timestamp,
       }, rows, None
   ```

   Every read happens inside one session, in the order written, never concurrently. A
   failure in any read fails that instance as a whole: it lands in `errors` and
   contributes nothing, rather than shipping a half-filled card. In `blocked` mode the
   per-status pages are merged and re-capped so an instance never contributes more than
   `length` rows regardless of mode. `earliest_timestamp` is read from the first page
   because it is instance-level, not filter-level (confirmed live).

8. **`app/routes/query_log.py` — combined counter and route.**

   ```python
   def _combined(summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
       if not summaries:
           return None
       total = sum(s["total"] for s in summaries)
       blocked = sum(s["blocked"] for s in summaries)
       return {"total": total, "blocked": blocked,
               "percent_blocked": round(blocked * 100 / total, 1) if total else 0.0,
               "instances_reporting": len(summaries)}


   @router.get("/query-log")
   def query_log(window: int = WINDOW_DEFAULT, length: int = LENGTH_DEFAULT,
                 domain: str = "", client: str = "", status: str = "") -> dict[str, Any]:
       try:
           query = QueryLogQuery(window=window, length=length, domain=domain, client=client, status=status)
       except ValidationError as exc:
           raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
       since = _now() - query.window
       instances = load_instances()
       results, errors, rows = {}, {}, []
       for name, payload, instance_rows, error in _fanout(instances, lambda inst: _query_log_instance(inst, query, since)):
           if error is not None:
               errors[name] = error
           else:
               results[name] = payload
               rows.extend(instance_rows)
       rows.sort(key=lambda r: (-r["time"], -r["id"], r["instance"]))
       return {
           "window": query.window, "length": query.length, "status": query.status,
           "domain": query.domain, "client": query.client, "since": since,
           "top_count": TOP_COUNT,
           "instance_names": [i.name for i in instances],
           "instances": results,
           "combined": _combined([r["summary"] for r in results.values()]),
           "queries": rows,
           "errors": errors,
       }
   ```

   The `ValidationError` wrap is needed because FastAPI does not convert a model built
   inside the handler. No `record(...)` call anywhere in this module.

9. **`app/main.py`** — import `query_log` alongside the other route modules and add
   `app.include_router(query_log.router)` after `lookup`.

10. **`frontend/src/api.ts` — types and client method.**

    ```ts
    export interface QuerySummary {
      total: number
      blocked: number
      percent_blocked: number
      unique_domains: number
      forwarded: number
      cached: number
      active_clients: number
      total_clients: number
      gravity_domains: number
      gravity_last_update: number
    }

    export interface TopDomain {
      domain: string
      count: number
    }

    export interface TopClient {
      ip: string
      name: string | null
      count: number
    }

    export interface UpstreamStat {
      ip: string | null
      name: string | null
      port: number
      count: number
      response: number
      variance: number
    }

    export interface QueryLogInstance {
      summary: QuerySummary
      top_domains: { permitted: TopDomain[]; blocked: TopDomain[] }
      top_clients: { permitted: TopClient[]; blocked: TopClient[] }
      upstreams: UpstreamStat[]
      records_filtered: number
      returned: number
      earliest_timestamp: number | null
    }

    export interface QueryLogRow {
      instance: string
      id: number
      time: number
      type: string
      domain: string
      cname: string | null
      status: string | null
      blocked: boolean
      client_ip: string
      client_name: string | null
      upstream: string | null
      reply_type: string | null
      reply_time: number | null
    }

    export interface QueryLogCombined {
      total: number
      blocked: number
      percent_blocked: number
      instances_reporting: number
    }

    export interface QueryLogFilters {
      window: number
      length: number
      domain: string
      client: string
      status: string
    }

    export interface QueryLogResponse extends QueryLogFilters {
      since: number
      top_count: number
      instance_names: string[]
      instances: Record<string, QueryLogInstance>
      combined: QueryLogCombined | null
      queries: QueryLogRow[]
      errors: Record<string, string>
    }
    ```

    ```ts
    queryLog: (filters: QueryLogFilters, signal?: AbortSignal) => {
      const params = new URLSearchParams({
        window: String(filters.window),
        length: String(filters.length),
        domain: filters.domain,
        client: filters.client,
        status: filters.status,
      })
      return request<QueryLogResponse>(`/api/query-log?${params.toString()}`, { signal })
    },
    ```

11. **`frontend/src/pages/QueriesPage.tsx`.** Functional component, no props. State:
    `draft: QueryLogFilters` (the form), `applied: QueryLogFilters` (what the last request
    used), `result?: QueryLogResponse`, `loading: boolean`, `tab: string`. Defaults:
    `window: 3600`, `length: 100`, `domain: ''`, `client: ''`, `status: ''`, `tab:
    'log'`. One `load(filters)` function holds an `AbortController` in a ref and a
    sequence counter, aborting any in-flight request before starting the next, exactly as
    `LookupPage.search` does. It runs once on mount with the defaults (with the
    `cancelled` guard and abort on unmount), on Apply (button or Enter in either input)
    with `draft`, and on Refresh with `applied`. No timer, no poll header. Switching tabs
    never fetches; every tab renders from the one `result`.

    Layout, top to bottom:
    - `page-heading` block: title "Queries", secondary paragraph "Recent DNS queries and
      24-hour statistics across every Pi-hole instance.", and a `ReloadOutlined` Refresh
      button with `loading={loading}`.
    - `div.query-filters` row of controls: `Select` window with options 5 minutes / 15
      minutes / 1 hour / 6 hours / 24 hours (values 300, 900, 3600, 21600, 86400);
      `Select` status with options "Any status" (`''`), "Blocked (any reason)"
      (`'blocked'`), a divider, then every entry of a local `QUERY_STATUS_OPTIONS` list
      mirroring `QUERY_STATUSES` in their FTL spelling; `Input` domain with `allowClear`
      and placeholder `*.doubleclick.net`; `Input` client with `allowClear` and
      placeholder `192.168.1.* or hostname`; `Select` per-instance limit with 50 / 100 /
      250 / 500; primary `Button` "Apply" with `FilterOutlined`. Enter in either `Input`
      applies. A secondary caption under the row reads "Filters and the window apply to
      the query log. Statistics always cover the last 24 hours."
    - One `Alert type="error"` per entry in `result.errors` (`dns-error` class,
      `"<instance> unavailable"`), matching the other pages.
    - `div.query-summary` grid of `Card`s. One per name in `result.instance_names` that
      has a result, titled with the instance name and subtitled "last 24 hours" via
      `extra`, holding `Statistic`s for Queries (`summary.total`), Blocked
      (`summary.blocked`, with `summary.percent_blocked` formatted to one decimal as the
      suffix), Active clients (`summary.active_clients`), and Domains on lists
      (`summary.gravity_domains`). When `result.combined` is non-null and
      `instance_names.length > 1`, a first card titled "All instances" shows Queries,
      Blocked with percent, and secondary text "{instances_reporting} of
      {instance_names.length} instances reporting" when they differ.
    - `Tabs` (`activeKey={tab}`, `onChange={setTab}`) with four items:

      **Query log** (`key: 'log'`). One `Table<QueryLogRow>` over `result.queries`,
      `rowKey={(row) => `${row.instance}:${row.id}`}`, `size="small"`, `scroll={{ x:
      'max-content' }}`, `pagination={{ pageSize: 50, showSizeChanger: false }}`.
      Columns: **Time** (`new Date(time * 1000).toLocaleTimeString()`, full date-time in
      `title`), **Instance** (`Tag`), **Domain** (`Typography.Text`, with `cname` as
      secondary text below when present), **Type**, **Status** (`Tag` — `error` when
      `blocked`, `success` for FORWARDED / CACHE / CACHE_STALE, `default` otherwise; shows
      the raw status string, `—` when `null`), **Client** (`client_name` with `client_ip`
      as secondary text below, or the ip alone when the name is `null`), **Upstream**
      (`—` when `null`), **Reply** (`reply_type`, with `reply_time` shown in milliseconds
      to one decimal as secondary text when present). Row keys are unique because
      `(instance, id)` is unique: `id` is FTL's per-instance database id and the instance
      name is the registry key. Below the table, one secondary line per instance whose
      `returned < records_filtered`: "{instance}: showing the newest {returned} of
      {records_filtered} matching queries. Narrow the window or filters to see the rest."
      And one per instance whose `earliest_timestamp` is non-null and greater than
      `result.since`: "{instance}: in-memory history starts at {time}, later than the
      selected window." The table's `emptyText` reads "No queries matched these filters".

      **Top domains** (`key: 'domains'`). One `Card` per instance with a result, titled
      with the instance name, containing a `div.query-top-pair` holding two small
      `Table<TopDomain>`s side by side titled "Permitted" and "Blocked" (`rowKey`
      `domain`, `pagination={false}`, `size="small"`). Columns: **#** (1-based rank),
      **Domain**, **Queries** (`count`), **Share** (percent to one decimal; permitted
      rows against `summary.total - summary.blocked`, blocked rows against
      `summary.blocked`, `—` when the denominator is 0). Each table's `emptyText` reads
      "No permitted queries" / "No blocked queries". A caption on the card reads "Top
      {top_count} in the last 24 hours".

      **Top clients** (`key: 'clients'`). Same structure over `Table<TopClient>` with
      `rowKey` `ip` and a **Client** column showing `name` with `ip` as secondary text
      below, or the ip alone when `name` is `null`.

      **Upstreams** (`key: 'upstreams'`). One merged `Table<UpstreamStat & { instance:
      string }>` built from every instance's `upstreams` with the instance name attached,
      `rowKey={(row) => `${row.instance}:${row.name ?? ''}:${row.ip ?? ''}:${row.port}`}`,
      sorted by instance then `count` descending, `pagination={false}`, `size="small"`.
      Columns: **Instance** (`Tag`), **Destination** (`name ?? ip ?? '—'`, with
      `ip:port` as secondary text when `port !== -1` and `ip` differs from `name`),
      **Queries** (`count`), **Share** (percent of `summary.total` to one decimal),
      **Avg response** (`response * 1000` to one decimal with unit `ms`, followed by
      `± {variance * 1000}` ms; `—` when `port === -1`). The `cache` and `blocklist` rows
      therefore show a name, a count, a share, and dashes.

    Split the summary card into `QuerySummaryCard` (props: `title: string`, `summary:
    QuerySummary`, `extra?: ReactNode`) and the two-table pair into `TopPairCard<T>`
    (props: `title: string`, `caption: string`, `permitted: T[]`, `blocked: T[]`,
    `columns: TableProps<T>['columns']`, `rowKey: keyof T & string`) in the same file,
    keeping `QueriesPage` to the form, the loop, the tabs, and the log table. The status
    options, the window options, and the share formatter are module-level constants and
    helpers.

12. **`frontend/src/App.tsx`** — import `QueriesPage` and `HistoryOutlined`; insert
    `{ key: 'queries', icon: <HistoryOutlined />, label: 'Queries' }` directly after
    `status`; add the `page === 'queries' ? <QueriesPage />` branch.

13. **`frontend/src/styles.css`** — add:

    ```css
    .query-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 8px; }
    .query-filters .ant-input-affix-wrapper, .query-filters .ant-select { min-width: 180px; }
    .query-summary { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; margin-bottom: 16px; }
    .query-top-pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    ```

    and inside the existing `@media (max-width: 767px)` block:
    `.query-filters { flex-direction: column; align-items: stretch; }` and
    `.query-top-pair { grid-template-columns: 1fr; }`.

14. **Docs.** `docs/design.md`: add §5.9 "Query log and statistics" after §5.8 —
    read-only, one fan-out per open or user action, one session per instance with the
    stats reads then the log reads inside it, blocked set is FTL's `is_blocked()`,
    single-status FTL limitation and the server-side expansion, window and length bounds,
    fixed 24-hour stats not tied to the window, per-instance top lists of 10 with no
    combined ranking, pseudo-upstreams kept, client-filter classification rule.
    `README.md` "Pi-hole compatibility": one paragraph after the Lookup paragraph saying
    the Queries page reads `/api/stats/summary`, `/api/stats/top_domains`,
    `/api/stats/top_clients`, `/api/stats/upstreams`, and `/api/queries` on every
    instance, merges the log newest-first, and is read-only. `docs/potential-features.md`:
    add a shipped bullet "Cross-instance query log with window, status, domain, and client
    filters, plus 24-hour per-instance counters, top domains, top clients, and upstreams —
    plan 0014 (design.md §5.9)"; remove the dashboard bullet from §2; rewrite "Suggested
    next pick" to name instance health widgets (`/api/info/system`, `/api/info/ftl`) as
    the next read-only item.

15. **Build.** `cd frontend && npm run build && npm run lint`; commit `app/static/ui/`.

## Contracts

**New route** `GET /api/query-log?window=<int>&length=<int>&domain=<str>&client=<str>&status=<str>`
— behind the auth middleware (path is not in the public set). No request body. Every
parameter is optional; validation is `QueryLogQuery` (Step 5) and a failed validation
returns 422 with a readable `detail` string.

Response (`200`) for `?window=3600&length=100&status=CACHE_STALE`, built from lab-pihole's
verbatim rows and stats on 2026-09-12 (top lists cut to three rows; `since` is
illustrative; lab-pihole has blocked nothing in its window so both blocked lists are
empty):

```json
{
  "window": 3600,
  "length": 100,
  "status": "CACHE_STALE",
  "domain": "",
  "client": "",
  "since": 1789264801.0,
  "top_count": 10,
  "instance_names": ["lab-pihole"],
  "instances": {
    "lab-pihole": {
      "summary": {
        "total": 156, "blocked": 0, "percent_blocked": 0, "unique_domains": 42,
        "forwarded": 100, "cached": 56, "active_clients": 2, "total_clients": 2,
        "gravity_domains": 79541, "gravity_last_update": 1788845689
      },
      "top_domains": {
        "permitted": [
          {"domain": "1.in-addr.arpa", "count": 25},
          {"domain": "1.0.0.127.in-addr.arpa", "count": 24},
          {"domain": "2.0.0.1.in-addr.arpa", "count": 18}
        ],
        "blocked": []
      },
      "top_clients": {
        "permitted": [
          {"ip": "::", "name": "pi.hole", "count": 97},
          {"ip": "127.0.0.1", "name": "localhost.lan", "count": 59}
        ],
        "blocked": []
      },
      "upstreams": [
        {"ip": "blocklist", "name": "blocklist", "port": -1, "count": 0, "response": 0, "variance": 0},
        {"ip": "cache", "name": "cache", "port": -1, "count": 56, "response": 0, "variance": 0},
        {"ip": "1.0.0.2", "name": "security.cloudflare-dns.com", "port": 53, "count": 62,
         "response": 0.020752204044927818, "variance": 0.0007558185700903987},
        {"ip": "1.1.1.2", "name": "security.cloudflare-dns.com", "port": 53, "count": 38,
         "response": 0.01903403675095718, "variance": 0.0006069763137027048}
      ],
      "records_filtered": 32,
      "returned": 2,
      "earliest_timestamp": 1789184400
    }
  },
  "combined": {"total": 156, "blocked": 0, "percent_blocked": 0.0, "instances_reporting": 1},
  "queries": [
    {"instance": "lab-pihole", "id": 37598335, "time": 1789268400.9673905, "type": "PTR",
     "domain": "2.1.1.1.in-addr.arpa", "cname": null, "status": "CACHE_STALE", "blocked": false,
     "client_ip": "127.0.0.1", "client_name": "localhost.lan", "upstream": null,
     "reply_type": "DOMAIN", "reply_time": 1.0967254638671875e-05},
    {"instance": "lab-pihole", "id": 37598334, "time": 1789268400.966724, "type": "PTR",
     "domain": "2.0.0.1.in-addr.arpa", "cname": null, "status": "CACHE_STALE", "blocked": false,
     "client_ip": "127.0.0.1", "client_name": "localhost.lan", "upstream": null,
     "reply_type": "DOMAIN", "reply_time": 8.34465026855469e-06}
  ],
  "errors": {}
}
```

A `FORWARDED` row from the same instance shows the non-null shape:
`{"instance": "lab-pihole", "id": 37598347, "time": 1789268401.1629233, "type": "DS",
"domain": "1.1.1.in-addr.arpa", "cname": null, "status": "FORWARDED", "blocked": false,
"client_ip": "::", "client_name": "pi.hole", "upstream": "1.0.0.2#53", "reply_type":
"NODATA", "reply_time": 0.0106356143951416}`.

`status` echoes `""`, `"blocked"`, or an uppercase FTL status. `queries` is sorted by
`time` descending, then `id` descending, then `instance` ascending, across all instances.
Each instance contributes at most `length` rows in any mode. Each top list holds at most
`top_count` rows, ranked by `count` descending as FTL returns them. `upstreams` is FTL's
order and includes the `port: -1` pseudo-entries. `combined` is `null` when no instance
responded. An instance appears in exactly one of `instances` or `errors`. The `api.ts`
side is the `Query*`, `Top*`, and `UpstreamStat` interfaces in Step 10, field for field.

**Pi-hole client** — `PiholeClient.get_stats_summary() -> StatsSummary`,
`get_top_domains(*, blocked, count) -> TopDomains`, `get_top_clients(*, blocked, count)
-> TopClients`, `get_upstreams() -> Upstreams`, and `get_queries(*, start, length,
domain, client_ip, client_name, status) -> QueryPage` (Steps 1–4). Outbound: `GET
/api/stats/summary`, `GET /api/stats/top_domains?blocked=…&count=…`, `GET
/api/stats/top_clients?blocked=…&count=…`, `GET /api/stats/upstreams`, and `GET
/api/queries?length=…[&from=…][&domain=…][&client_ip=…][&client_name=…][&status=…]`.

No config keys, no DB or data-directory changes, no audit entries.

## Tests

- `tests/test_pihole_client.py::test_get_stats_summary_parses_confirmed_shape` — the
  verbatim lab summary body parses into a `StatsSummary` with `total == 156`,
  `active_clients == 2`, `gravity_domains == 79541`, `percent_blocked == 0`.
- `tests/test_pihole_client.py::test_get_stats_summary_rejects_invalid_response` —
  parametrized: a body missing `queries.total`, one with `queries.blocked` as a bool, one
  missing `gravity`, and a non-200 status each raise `PiholeAPIError`; the non-200 message
  carries the status code.
- `tests/test_pihole_client.py::test_top_lists_send_blocked_flag_and_count` —
  `MockTransport` asserts `get_top_domains(blocked=True, count=10)` requests
  `/api/stats/top_domains` with `blocked=true&count=10` and `get_top_clients(blocked=False,
  count=10)` requests `/api/stats/top_clients` with `blocked=false&count=10`; the verbatim
  lab bodies parse into `TopDomains` (`domains[0].domain == "1.in-addr.arpa"`, `count ==
  25`, `total_queries == 156`) and `TopClients` (`clients[0].ip == "::"`, `name ==
  "pi.hole"`); a client item with `name: null` yields `name is None`.
- `tests/test_pihole_client.py::test_top_lists_reject_invalid_response` — parametrized
  over both methods: a body without the list key, an item missing `count`, an item with a
  bool `count`, a body missing `total_queries`, and a non-200 status each raise
  `PiholeAPIError`.
- `tests/test_pihole_client.py::test_get_upstreams_keeps_pseudo_entries_and_statistics` —
  the verbatim lab body parses into four `Upstream`s; the `cache` row has `port == -1` and
  `response == 0`; the `1.0.0.2` row has `port == 53`, `name ==
  "security.cloudflare-dns.com"`, and the float `response` and `variance`;
  `forwarded_queries == 100`; an item with `ip: null` and `name: null` parses with both
  `None`.
- `tests/test_pihole_client.py::test_get_upstreams_rejects_invalid_response` —
  parametrized: a body without `upstreams`, an item missing `statistics`, an item whose
  `statistics.response` is a string, a bool `port`, and a non-200 status each raise
  `PiholeAPIError`.
- `tests/test_pihole_client.py::test_get_queries_sends_only_provided_filters` —
  `MockTransport` asserts a call with `start=1700000000.7, length=50, domain="*.example",
  status="GRAVITY"` sends `length=50`, `from=1700000000`, `domain=*.example`,
  `status=GRAVITY`, and no `client_ip`, `client_name`, or `until` keys; a call with only
  `length` sends exactly `length`.
- `tests/test_pihole_client.py::test_get_queries_parses_records_and_metadata` — the
  verbatim two-row lab body parses into a `QueryPage` with `records_filtered == 32`,
  `earliest_timestamp == 1789184400`, `queries[0].upstream is None`, `client_name ==
  "localhost.lan"`, `reply_type == "DOMAIN"`; a row with `client.name: null` and `reply:
  {}` yields `client_name is None` and `reply_type is None`.
- `tests/test_pihole_client.py::test_get_queries_rejects_invalid_response` — parametrized:
  a body without `queries`, one with `recordsFiltered` missing, one whose item lacks `id`,
  one whose item has `client` without `ip`, and a non-200 status each raise
  `PiholeAPIError`.
- `tests/test_query_log.py::test_blocked_statuses_match_ftl_is_blocked` — asserts
  `BLOCKED_STATUSES` equals the literal twelve-name set from Approach and is a subset of
  `QUERY_STATUSES`.
- `tests/test_query_log.py::test_query_log_fans_out_and_isolates_instance_errors` — two
  instances; the fake raises `PiholeAPIError("stats failed")` for `pihole2`; `pihole1`
  lands in `instances` with its summary, top lists, upstreams, and rows, `pihole2` in
  `errors`, `instance_names` lists both, `combined.instances_reporting == 1`.
- `tests/test_query_log.py::test_any_failed_stat_read_fails_that_instance_only` — the
  fake raises only from `get_upstreams` for `pihole1`; `pihole1` lands in `errors` with
  the message and contributes no rows, `pihole2` is complete.
- `tests/test_query_log.py::test_query_log_unreachable_instance_hides_transport_detail` —
  a fake raising `httpx.ConnectError` yields `"Unable to reach Pi-hole instance"`.
- `tests/test_query_log.py::test_reads_happen_in_order_inside_one_session` — the fake
  records `enter`, then each method name with its kwargs, then `exit`, per instance; the
  sequence is exactly `summary`, `top_domains(blocked=False, count=10)`,
  `top_domains(blocked=True, count=10)`, `top_clients(blocked=False, count=10)`,
  `top_clients(blocked=True, count=10)`, `upstreams`, `queries`, and there is one
  enter/exit pair per instance.
- `tests/test_query_log.py::test_stats_are_serialized_per_instance` — the fake returns
  the confirmed lab shapes; the payload's `top_domains.permitted`, `top_domains.blocked`,
  `top_clients.permitted`, `top_clients.blocked`, and `upstreams` match the Contracts
  example field for field, and `top_count == 10` at the top level.
- `tests/test_query_log.py::test_rows_merge_newest_first_across_instances` — `pihole1`
  returns rows at times 30 and 10, `pihole2` at 20 and 30 with a higher id; the merged
  order is `pihole2@30`, `pihole1@30`, `pihole2@20`, `pihole1@10`, every row carries its
  `instance`, and `blocked` is `True` exactly for rows whose status is in
  `BLOCKED_STATUSES`.
- `tests/test_query_log.py::test_blocked_filter_issues_one_call_per_blocked_status` —
  with `status=blocked` the fake's recorded `status` arguments for one instance equal
  `sorted(BLOCKED_STATUSES)` exactly once each; `records_filtered` is the sum of the
  per-status values; rows from two statuses merge newest-first and are capped at `length`
  (`length=3` with 2 GRAVITY and 2 REGEX rows returns 3); `earliest_timestamp` is taken
  from the first page.
- `tests/test_query_log.py::test_specific_status_is_uppercased_and_passed_through` —
  `status="cache_stale"` produces one call with `status="CACHE_STALE"` and the response
  echoes `"CACHE_STALE"`; `status=""` produces one call with `status=None` and echoes `""`.
- `tests/test_query_log.py::test_status_validation` — `status="BOGUS"` raises a 422
  `HTTPException` with a readable detail.
- `tests/test_query_log.py::test_window_and_length_bounds` — parametrized: `window` 59
  and 86401, `length` 0 and 501 each raise 422; defaults are `window=3600`,
  `length=100`; the fake receives `length=100` and a `start` equal to `_now() - 3600`
  under a monkeypatched `_now` returning a fixed value, and the response's `since`
  matches.
- `tests/test_query_log.py::test_client_filter_routes_ip_and_name` — parametrized over
  `("192.168.1.5", ip)`, `("192.168.*", ip)`, `("fe80::1", ip)`, `("::", ip)`, `("nas",
  name)`, `("nas.lan", name)`, `("*phone*", name)`, `("", neither)`; asserts
  `_client_filter` directly and that the fake receives exactly one of `client_ip` /
  `client_name` set.
- `tests/test_query_log.py::test_domain_and_client_filters_are_normalized_and_validated`
  — `" *.Example.COM "` reaches the fake as `*.example.com` and is echoed; `"a b"`,
  `"a/b"` for `domain` and `"a b"` for `client` each raise 422; an empty domain reaches
  the fake as `None`.
- `tests/test_query_log.py::test_combined_counts_reporting_instances_only` — two
  instances with totals 100/20 and 300/30 give `combined == {"total": 400, "blocked": 50,
  "percent_blocked": 12.5, "instances_reporting": 2}`; when one errors, `combined` covers
  the other only; when both total `0`, `percent_blocked == 0.0`; when both error,
  `combined is None`.
- `tests/test_query_log.py::test_query_log_module_has_no_audit_path` — asserts
  `not hasattr(query_log, "record")`.

The fake client for `test_query_log.py` follows `FakeLookupPiholeClient`'s shape:
class-level `summaries: dict[str, StatsSummary]`, `top_domains` and `top_clients` keyed by
instance then `blocked` flag, `upstreams: dict[str, Upstreams]`, `pages: dict[str,
dict[str | None, QueryPage]]` keyed by instance then status, `fail: dict[str, Exception]`
plus an optional `fail_on: dict[str, str]` naming the method that should raise, and a
`calls` list capturing `(name, method, kwargs)` in call order with `enter` / `exit`
markers. Unset entries fall back to empty lists and zeroed counters so a test only sets
what it asserts on.

## Invariants at risk

1. **One instance mutated at a time** — nothing is mutated. The fan-out is reads only,
   and the per-status fan-in inside an instance is also reads only.
2. **Diff before write** — no writes.
3. **Gravity gated behind real changes** — no gravity call; the route never touches
   `mark_gravity_pending`.
4. **No background scheduler** — the page loads on open and on Refresh or Apply. No
   timer, no poll header, no startup job. The Status page's 10-second refresh is not
   copied here.
5. **Credentials stay in the backend** — the response carries query rows, counters, top
   lists, upstream addresses, and instance names only; `_client` resolves the password
   server-side as everywhere else. Upstream addresses are the Pi-hole's forwarders, not
   this app's instance URLs.
6. **All outbound through `url_policy`** — all five new client methods use the existing
   `PiholeClient` and its `_PinnedTransport`; no new `httpx` client and no user-supplied
   URL. User-typed filters travel as query parameters through `httpx`'s own encoding,
   never as path segments, and whitespace and `/` are rejected before that. `count` is
   a server constant.
7. **Every route behind auth** — `/api/query-log` is not added to the public set in
   `auth_middleware.py`; the middleware is prefix-based and unchanged.
8. **Compiled frontend committed** — Step 15.
9. **`main` is production** — work lands on `feature/0014-query-log`.

## Acceptance criteria

- [ ] `GET /api/query-log` against lab-pihole with defaults returns one `instances` entry
      whose `summary.total` matches the Pi-hole dashboard's 24-hour total, rows sorted
      newest-first, and `combined.instances_reporting == 1`.
- [ ] The same response's `top_domains.permitted` and `top_clients.permitted` match the
      Pi-hole dashboard's top permitted lists in order, each with at most 10 rows, and
      `upstreams` lists `blocklist`, `cache`, and each real forwarder with `port: 53` and
      a non-zero `response`.
- [ ] `?status=blocked&window=86400` returns only rows with `blocked: true`, and its
      per-instance `records_filtered` matches the sum of the twelve blocked-status counters
      in that instance's `/api/stats/summary` `queries.status` map, allowing for queries
      that arrive between the two reads.
- [ ] `?status=cache_stale` returns only `CACHE_STALE` rows and echoes `"CACHE_STALE"`;
      `?status=bogus` returns 422 with a readable message that the browser shows.
- [ ] `?domain=*arpa` narrows rows and `records_filtered` on lab-pihole; `?client=::` and
      `?client=pi.hole` each match the same count (97 at probe time), proving the ip / name
      routing.
- [ ] `?window=59`, `?window=86401`, `?length=0`, `?length=501` each return 422.
- [ ] With `length=5` and more than five matching rows, `returned == 5`,
      `records_filtered` is the larger true count, and the page shows the "showing the
      newest 5 of N" line for that instance.
- [ ] Two instances' rows interleave by time in one table (exercised by the unit test; a
      second live instance is not required).
- [ ] Row keys are unique in all four tabs: no duplicate-key warning in the browser
      console with a full log table and populated top lists and upstreams.
- [ ] Changing the window or a filter and applying changes the log tab and the
      "showing" lines only; the summary cards and the three stats tabs show the same
      numbers before and after (allowing for new queries arriving).
- [ ] Switching tabs issues no request to `/api/query-log`.
- [ ] Unauthenticated `GET /api/query-log` is rejected by the auth middleware.
- [ ] The Queries page renders the filter bar, one summary card per instance, the four
      tabs with instance tags and status colours, and the unavailable alert when an
      instance errors.
- [ ] Applying new filters while a load is in flight aborts the first request; no stale
      result overwrites the newer one.
- [ ] Leaving the page open for several minutes produces no further requests to
      `/api/query-log` (no timer).
- [ ] No audit line is written by a query-log read (`tail` the audit log across a load).
- [ ] `./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
- [ ] design.md §5.9, README, and potential-features.md updated as in Step 14.

Several acceptance checks need the keyring-backed credential for `lab-pihole`, which is
not reachable from Codex's sandbox ("No credential stored"), so they fall to the reviewer
running the app locally rather than to the implementer.

**Live verification, 2026-09-13 (reviewer, against lab-pihole through the real route
function).** Defaults returned the 24-hour summary, ten permitted top domains, both top
clients, and all four upstreams including the two `port: -1` pseudo-entries, with rows
sorted newest-first. `status=blocked&window=86400` returned zero rows and a
`records_filtered` of 0, matching the summary's blocked-status counter sum of 0 (the lab
instance blocks nothing). `status=cache_stale` returned only `CACHE_STALE` rows and echoed
`"CACHE_STALE"`; `status=bogus` returned 422. `domain=*arpa` narrowed to 155 of 156;
`client=::` and `client=pi.hole` each matched 97. All four window and length bounds
returned 422. `length=5` returned 5 of 156. The audit log was not written. A fresh
`npm run build` reproduced the committed bundle byte for byte. Browser-only checks (tab
switching issues no request, abort on re-apply, no timer, no duplicate-key warnings)
remain for a manual pass.
