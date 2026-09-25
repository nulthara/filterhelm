# 0022 — In-app audit log viewer

Status: shipped          <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0022-audit-log-viewer
Author: Claude (architect)
Date: 2026-09-25

## Problem

Every mutation and sign-in event lands in `audit.log` (`app/audit.py`), one JSON object per
line. The file is private (`0600`), so the only way to read it is to open a shell on the host
and grep. That is a lot of effort for the log's main job, which is answering "what changed,
where, and when" after something looks wrong. `app/audit.py` already has a `recent()` reader,
but no route calls it. The app needs a read-only, filterable view of the log, behind the
existing login.

## Scope

In:
- `GET /api/audit-log` returns the newest matching entries, newest first. The filters are
  instance, action, actor, and free text, plus a result limit. Filtering runs server-side
  over the whole file.
- The response also lists the distinct instances, actions, and actors in the file, so the
  filter dropdowns fill from real data.
- A new "Audit log" page and sidebar entry. It has a filter bar and a table with one row per
  entry, and each row expands to show the entry's before and after values.
- `app/audit.py` gains `search()`. The unused `recent()` is removed.
- Docs updated in `docs/design.md` (new §5.11), `README.md` (§ Request integrity), and
  `docs/potential-features.md`.

Out:
- Writing to, rotating, truncating, or exporting the log. The viewer never opens the file
  for writing.
- Date-range filters. Newest-first order plus the limit covers the "what just happened"
  case. Add a date range later if the log grows past what that serves.
- Changing the audit entry format or adding audit coverage for actions not audited today.
- Auditing the viewer's own reads.
- Polling or auto-refresh. Refresh is an explicit button.
- Rollback or "revert this" actions (a separate backlog item).
- Frontend test tooling.

## Approach

**Server-side filtering over a full-file scan.** `search()` reads `AUDIT_FILE` line by line,
parses each line, keeps entries that match every active filter, and returns the newest
`limit` of them in reverse order. It also returns the total match count. The log is
append-only at about 250 bytes per entry, and the local copy holds 94 lines, so a full scan
per request is cheap. The scan still lets a filter find an entry of any age. Rejected
alternative: send only the newest N entries and filter them in the browser. Older entries
would then be unreachable (user decision).

**Its own page, not a Settings section.** The log covers every instance plus `application`
events such as logins. Settings is built around one selected instance and an edit form, so
the log would sit awkwardly under that picker (user decision).

**Text search matches values, not raw JSON.** A raw-JSON substring search would miss
`(\.|^)com$`, because JSON escapes the backslash. `_haystack(entry)` joins the instance,
action, and actor with every key and scalar leaf of `before` and `after`, using `str()`
without JSON escaping. The match is a case-insensitive substring test.

**Partial or malformed lines are skipped and counted.** `record()` appends without a lock.
A read that races an append can therefore see a partial last line. A line that fails
`json.loads`, or that parses to something other than an object, is skipped and adds one to
`skipped`. The page shows a notice when `skipped > 0`. Missing keys on a valid object get
defaults (`before`/`after` → `{}`, `actor` → `null`, strings → `""`), so an old or hand-edited
line cannot break the table.

**Exact-match dropdown filters.** Instance, action, and actor are exact string matches on
values taken from the facets. An empty string means "any". The actor facet leaves out
`null`, which pre-login events carry.

**Response shape.** The route returns `dict[str, Any]` and validates its query parameters
through a Pydantic model that is converted to a 422. This follows the precedent at
`app/routes/query_log.py:165-171` (`QueryLogQuery` plus the `dict[str, Any]` return). No
per-entry response model is used, because `before`/`after` are free-form by design.

**Module name.** The route module is `app/routes/audit_log.py`. The name keeps it distinct
from `app/audit.py`, which five route modules import `record` from.

## Files

- `app/audit.py` — add `search()` and `_haystack()`, remove the unused `recent()`.
- `app/routes/audit_log.py` — new. `AuditLogQuery` model and the `GET /api/audit-log` route.
- `app/main.py` — import and `include_router(audit_log.router)`.
- `tests/test_audit.py` — new. Tests for `search()` and the route.
- `frontend/src/api.ts` — `AuditEntry`, `AuditLogFilters`, `AuditLogResponse` types and the
  `api.auditLog()` client.
- `frontend/src/pages/AuditPage.tsx` — new page.
- `frontend/src/App.tsx` — sidebar entry and page switch.
- `app/static/ui/` — rebuilt frontend output.
- `docs/design.md` — new §5.11 "Audit log viewer".
- `README.md` — one sentence in the "Request integrity" audit paragraph.
- `docs/potential-features.md` — move the item to the "Shipped" list and remove it from §2.

## Steps

1. **`app/audit.py`: add `search()`, remove `recent()`.**

   ```python
   AUDIT_LIMIT_DEFAULT, AUDIT_LIMIT_MAX = 200, 1000

   def search(*, instance: str = "", action: str = "", actor: str = "",
              text: str = "", limit: int = AUDIT_LIMIT_DEFAULT) -> dict[str, Any]:
   ```

   - If `AUDIT_FILE` does not exist, return the empty result: `entries=[]`, `matched=0`,
     `skipped=0`, and empty facet lists.
   - Open the file read-only (`"r"`, `encoding="utf-8"`, `errors="replace"`) and iterate
     over its lines. Skip blank lines without counting them. Count a line in `skipped` when
     `json.loads` raises `ValueError` or the result is not a `dict`.
   - Normalize each valid entry to `{"ts": str, "instance": str, "action": str,
     "actor": str | None, "before": dict, "after": dict}`. Use the defaults listed in
     Approach, and replace a non-dict `before`/`after` with `{}`.
   - Collect the facets from every valid entry before filtering: sorted distinct
     `instance`, `action`, and non-null `actor` values.
   - Filter. `instance`/`action`/`actor` must match exactly when non-empty. When `text` is
     non-empty, `text.lower()` must be a substring of `_haystack(entry)`.
   - Build the result: `matched` = number of filtered entries, and `entries` = the last
     `limit` filtered entries, reversed so the newest comes first. File order is append
     order, so it counts as chronological. Do not sort by `ts`.
   - Return `{"entries", "matched", "skipped", "instances", "actions", "actors"}`.
   - Let an `OSError` from `open()` or the read propagate, so the route can map it.

   ```python
   def _haystack(entry: dict[str, Any]) -> str:
   ```

   Returns the lowercased, newline-joined strings of `instance`, `action`, `actor` (when
   non-null), and every dict key and scalar leaf (`str`, `int`, `float`, `bool`) found by
   recursing through `before` and `after`, including lists.

   Delete `recent()`. Nothing imports it (`grep -rn "recent" app tests` finds only its
   definition).

2. **`app/routes/audit_log.py`: new route.**

   ```python
   class AuditLogQuery(BaseModel):
       instance: str = Field(default="", max_length=64)
       action: str = Field(default="", max_length=64)
       actor: str = Field(default="", max_length=128)
       text: str = Field(default="", max_length=200)
       limit: int = Field(default=AUDIT_LIMIT_DEFAULT, ge=1, le=AUDIT_LIMIT_MAX)
   ```

   Each string field strips surrounding whitespace in a `field_validator`.

   ```python
   @router.get("/audit-log")
   def audit_log(instance: str = "", action: str = "", actor: str = "", text: str = "",
                 limit: int = AUDIT_LIMIT_DEFAULT) -> dict[str, Any]:
   ```

   - Build `AuditLogQuery`. On `ValidationError`, raise `HTTPException(422,
     detail=exc.errors()[0]["msg"])`, matching `query_log`.
   - Call `audit.search(**query.model_dump())`. Call it through the module (`from app import
     audit`; `audit.search(...)`), so tests can monkeypatch `audit.AUDIT_FILE`.
   - On `OSError`, log it with `logger.exception("audit log read failed")` and raise
     `HTTPException(503, detail="Audit log could not be read")`. Do not put the file path in
     the detail.
   - Return the search result plus the echoed filters and `returned = len(entries)`.
     Contracts gives the exact shape.

   `router = APIRouter(prefix="/api")` and `logger = logging.getLogger(__name__)`. There are no credentials, no `PiholeClient`, and no
   outbound calls.

3. **`app/main.py`: register the router.** Add `audit_log` to the `from app.routes import ...`
   line and add `app.include_router(audit_log.router)` after `query_log`.

4. **`frontend/src/api.ts`: types and client.** Add the types from Contracts. Add
   `auditLog(filters: AuditLogFilters, signal?: AbortSignal)` to `api`, next to
   `queryLog`. It builds `URLSearchParams` from all five filters, the same way `queryLog`
   does (empty strings are sent and the backend treats them as "any"), and calls
   `request<AuditLogResponse>('/api/audit-log?…', { signal })`.

5. **`frontend/src/pages/AuditPage.tsx`: new page.** The fetch lifecycle mirrors
   `QueriesPage` (`controllerRef` with `AbortController`, a `load(filters)` function, and an
   initial load through `useEffectEvent` + `useEffect`). The previous request is aborted
   before a new one starts, and the controller is aborted on unmount.

   - The heading is "Audit log", with the secondary text "Every change and sign-in this app
     has recorded, newest first. Read-only."
   - The filter bar is a `Space wrap` containing:
     - `Select` for Instance, Action, and Actor. Each has `allowClear`, a placeholder
       ("All instances" / "All actions" / "All actors"), and options taken from the last
       response's `instances` / `actions` / `actors`. A change reloads right away.
     - `Input.Search` for text, with placeholder "Search values". `onSearch` reloads, so a
       keystroke does not fetch.
     - A `Button` labelled "Refresh" with `ReloadOutlined`, which reloads with the current
       filters.
   - Clearing a Select sends `""` for that filter.
   - Above the table, a secondary `Typography.Text` reads "Showing {returned} of {matched}
     matching entries". When `matched > returned` it adds " (newest {limit})".
   - When `skipped > 0`, show `Alert type="warning"` with "{skipped} unreadable line(s) in
     the audit log were skipped."
   - On a request error (other than an abort), show `Alert type="error"`, message "Audit log
     unavailable", with the error text as the description.
   - The `Table` has `rowKey` = the entry's index in the current response
     (`${index}-${ts}`), `size="small"`, and `pagination={{ pageSize: 50 }}`. Its columns:
     - **Time** — `new Date(ts).toLocaleString()`. An unparseable `ts` shows the raw string.
     - **Instance** — plain text.
     - **Action** — `Tag`.
     - **Actor** — the actor, or secondary "—" when it is `null`.
   - `expandable.expandedRowRender` shows two side-by-side blocks, "Before" and "After".
     Each holds a `<pre>` of `JSON.stringify(value, null, 2)`, or secondary "(empty)" when
     the object has no keys.
   - `Empty` shows "No audit entries match" when `entries` is empty and nothing is loading.

   Define a props interface only if the page takes props. It does not, so none is needed.

6. **`frontend/src/App.tsx`: navigation.** Import `AuditPage` and `AuditOutlined`. Add the
   menu item `{ key: 'audit', icon: <AuditOutlined />, label: 'Audit log' }` after
   `settings`. Add the branch `page === 'audit' ? <AuditPage /> :` before the final
   `<SettingsPage />` fallback.

7. **Build.** `cd frontend && npm run build && npm run lint`, then commit `app/static/ui/`.

8. **Docs.**
   - `docs/design.md`: add §5.11 "Audit log viewer" after §5.10. Cover the read-only
     `GET /api/audit-log`, the server-side exact filters plus value-level text search, the
     newest-first limit (default 200, max 1000) with an exact `matched` count, the
     skipped-line count, explicit refresh with no polling, and no audit entry for viewing.
   - `README.md` § Request integrity: after the sentence ending "never records passwords or
     session tokens.", add "Signed-in users can read and filter it on the Audit log page."
   - `docs/potential-features.md`: remove the §2 bullet and add a "Shipped" entry, "In-app
     audit log viewer with instance, action, actor, and text filters — plan 0022 (design.md
     §5.11)."

## Contracts

**Backend — `GET /api/audit-log`**

Query parameters, all optional:

| Param | Type | Default | Rule |
| --- | --- | --- | --- |
| `instance` | str | `""` | ≤ 64 chars, stripped; exact match; `""` = any |
| `action` | str | `""` | ≤ 64 chars, stripped; exact match; `""` = any |
| `actor` | str | `""` | ≤ 128 chars, stripped; exact match; `""` = any |
| `text` | str | `""` | ≤ 200 chars, stripped; case-insensitive substring of `_haystack` |
| `limit` | int | `200` | 1–1000 |

Response `200`:

```json
{
  "instance": "", "action": "", "actor": "", "text": "", "limit": 200,
  "entries": [
    {"ts": "2026-09-20T03:21:45.302598+00:00", "instance": "application",
     "action": "login", "actor": null,
     "before": {}, "after": {"username": "admin", "remote_addr": "127.0.0.1"}}
  ],
  "matched": 94, "returned": 94, "skipped": 0,
  "instances": ["application", "lab-pihole"],
  "actions": ["adlist_add", "login"],
  "actors": ["admin"]
}
```

Errors: `422` with `{"detail": "<first validation message>"}`, `503` with
`{"detail": "Audit log could not be read"}`, and `401` from the auth middleware when not
signed in.

**Frontend — `frontend/src/api.ts`**

```ts
export interface AuditEntry {
  ts: string
  instance: string
  action: string
  actor: string | null
  before: Record<string, unknown>
  after: Record<string, unknown>
}

export interface AuditLogFilters {
  instance: string
  action: string
  actor: string
  text: string
  limit: number
}

export interface AuditLogResponse extends AuditLogFilters {
  entries: AuditEntry[]
  matched: number
  returned: number
  skipped: number
  instances: string[]
  actions: string[]
  actors: string[]
}
```

`api.auditLog(filters: AuditLogFilters, signal?: AbortSignal): Promise<AuditLogResponse>`

No config key or DB change. The audit file format does not change.

## Tests

New file `tests/test_audit.py`. Each test monkeypatches `app.audit.AUDIT_FILE` to a
`tmp_path` file and writes fixture lines directly, or through `audit.record()` with
`get_current_user` patched.

- `test_search_returns_newest_first_with_facets` — three entries across two instances and
  one `application` login with `actor: null`. `entries` are in reverse file order.
  `matched == 3`. `instances`, `actions`, and `actors` are sorted, distinct, and `actors`
  leaves out `null`.
- `test_search_missing_file_returns_empty` — no file gives zero entries, `matched == 0`,
  `skipped == 0`, and empty facets.
- `test_search_exact_filters_combine` — `instance` + `action` + `actor` together return only
  the entry that matches all three. A partial value (`"lab"` for `"lab-pihole"`) matches
  nothing.
- `test_search_text_matches_nested_values_case_insensitively` — `"NAS.LAN"` matches an entry
  whose `after["dns.hosts"]` is `"192.168.1.10 nas.lan"`. `(\.|^)com$` matches a
  regex-domain entry, which proves the search does not run over JSON-escaped text. A key
  name (`"remote_addr"`) matches too. Text also matches `instance`/`action`.
- `test_search_limit_caps_entries_but_counts_every_match` — 5 matches with `limit=2` gives
  the 2 newest and `matched == 5`.
- `test_search_skips_malformed_and_partial_lines` — a non-JSON line, a JSON array line, and a
  truncated last line (`{"ts": "2026-`) give `skipped == 3`, and the valid entries still
  return. Blank lines are not counted.
- `test_search_defaults_missing_fields` — `{"action": "login"}` returns with `instance ""`,
  `actor None`, and `before == after == {}`. A string-valued `before` becomes `{}`.
- `test_search_round_trips_record` — `audit.record()` twice, then `search()` returns both,
  newest first, with the recorded actor.
- `test_audit_log_route_echoes_filters_and_returned` — the route result has the filter echo,
  `returned == len(entries)`, and the stripped text value.
- `test_audit_log_route_rejects_bad_params` — parametrized over `limit=0`, `limit=1001`, and
  `text` of 201 chars. Each raises `HTTPException` with status 422.
- `test_audit_log_route_unreadable_file_returns_503` — patch `audit.search` to raise
  `PermissionError`. The result is a 503 whose detail does not contain the tmp path.
- `test_audit_log_route_requires_authentication` — `FastAPI` with `AuthenticationMiddleware`
  (local mode, no session), following `tests/test_query_log.py:240-257`. `GET
  /api/audit-log` returns 401 `{"detail": "Authentication required"}`, and a patched
  `audit.search` spy is never called.
- `test_audit_log_route_does_not_modify_the_file` — the file's content and `st_mtime_ns` are
  unchanged after a route call.

## Invariants at risk

1. **One instance mutated at a time** — not touched. The feature makes no Pi-hole call.
2. **Diff before write** — not touched. There is no write path.
3. **Gravity gated on real changes** — not touched.
4. **No background scheduler** — the page fetches on mount, on a filter change, and on
   Refresh, with no timer or polling. The backend adds no startup work.
5. **Credentials never reach the browser** — the route returns audit entries verbatim.
   `record()` callers log instance names, usernames, client addresses, DNS names, and
   setting values, but never passwords, app-passwords, session tokens, or instance base
   URLs (README § Request integrity). The route adds no fields beyond the log's own. The
   503 detail leaves out the file path.
6. **Outbound URLs through `url_policy`** — not touched. The route makes no outbound request.
7. **Every route behind auth** — `/api/audit-log` is not in `AuthenticationMiddleware`'s
   public set (`app/auth_middleware.py:24`). A test asserts the 401.
8. **Compiled frontend committed** — Step 7 rebuilds and commits `app/static/ui/`.
9. **`main` is production** — work stays on `feature/0022-audit-log-viewer`.

## Acceptance criteria

- [ ] `GET /api/audit-log` with no params returns the newest ≤ 200 entries, newest first,
      with facets and an exact `matched` count.
- [ ] Each filter narrows the results. Text search finds a value that contains a backslash.
- [ ] Unauthenticated `GET /api/audit-log` → 401, and the file is not read.
- [ ] Bad params → 422. An unreadable file → 503 with no path in the detail.
- [ ] A partial or malformed line is skipped, counted, and flagged on the page. It does not
      error.
- [ ] The viewer never writes the audit file (content and mtime unchanged).
- [ ] The sidebar shows "Audit log". The page loads, the dropdowns fill from real data, rows
      expand to Before/After JSON, and Refresh refetches. There is no polling (check the
      network tab while idle).
- [ ] `recent()` is gone from `app/audit.py`, and nothing references it.
- [ ] `docs/design.md` §5.11, `README.md`, and `docs/potential-features.md` are updated.
- [ ] `./.venv/bin/pytest` green (baseline 520 passed, plus the new tests).
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
