# 0013 — "Why is this blocked/allowed?" domain lookup

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Revision 1: 2026-09-11 — Codex stopped at the plan-review gate with two correct findings, both fixed here before any code was written; the plan needs re-approval. (a) Step 6 computed the verdict from result arrays that `N` may have capped, so an omitted higher-precedence entry could produce a confidently wrong answer — the worst failure this feature can have, since the verdict is the whole product. Fixed two ways: exact searches now use a much larger cap than partial ones (`LOOKUP_LIMIT_EXACT = 1000` vs `LOOKUP_LIMIT_PARTIAL = 100`, Step 4), and a truncated exact search withholds the verdict rather than guessing (Step 6), joining partial searches in the `verdict: null` case the response already models. (b) The Contracts example was internally inconsistent — it listed an enabled exact allow entry while claiming `blocked`, which the Step 5 precedence order contradicts, and its counts disagreed with the rows shown. It was a paste of one live query's entries under another's verdict; replaced with the verbatim `doubleclick.net` response confirmed on lab-pihole. Scope and the Approach's design decisions are otherwise unchanged.
Revision 2: 2026-09-11 — Codex stopped at the plan-review gate a second time with two more correct findings, both fixed here before any code was written; the plan needs re-approval. (a) Revision 1's truncation guard had a hole underneath it. Step 3 defaulted missing or non-integer `results` values to `0`, including `results.total`, and Step 6 derives `truncated` from that total — so a malformed response would compute `0 > returned` as false, declare the result complete, and hand out exactly the confident wrong verdict Revision 1 existed to prevent. `results.total` is completeness metadata, not a display number, so all five counts are now required and a malformed `results` raises `PiholeAPIError`, surfacing as a per-instance error. This also restores consistency with `get_adlists` / `get_domains` / `get_groups`, which already raise rather than default. (b) Step 9's truncation message said "Showing the first {limit} of {counts.total}" above a table holding both arrays concatenated. Since `N` caps each array separately — confirmed live and already recorded in Approach — a fully capped response shows up to two caps' worth of rows, so the message would claim 100 above a 200-row table. Both messages now count the rows actually rendered. Scope and every earlier design decision are unchanged.
Revision 3: 2026-09-11 — Codex implemented the plan, passed every check (385 pytest tests, frontend build and lint), then stopped before committing on one more correct Step 9 finding. The table's row key was `${source}:${entry}`, which collapses whenever one domain appears in several gravity lists — the ordinary case with more than one block list, since `gravity[]` returns a row per (list, domain) pair — and again when an exact and a regex entry of the same type share a literal string. Duplicate React keys make the table reuse the wrong row. Step 9 now specifies the composite keys `domain:{type}:{kind}:{domain}` and `gravity:{address}:{domain}`, both built from fields already in the contract. This is a clarification of one line, not a design change, so the plan stays `in-progress` and went back to the same Codex thread rather than through re-approval. Scope, contracts, and every earlier decision are unchanged.
Branch: feature/0013-domain-lookup
Author: Claude (architect)
Date: 2026-09-11

## Problem

When a domain is blocked (or unexpectedly not blocked), finding out *which* entry did it
means SSHing to a Pi-hole and running `pihole -q`, or scrolling the Domains and Block
Lists pages by hand. Neither answers the question across instances at once, and neither
sees inside gravity's ~80k-domain lists. Pi-hole v6 exposes exactly the lookup `pihole -q`
uses, `GET /api/search/{domain}`, and this app does not call it. A read-only Lookup page
that fans that call out across every instance and explains the result is the
troubleshooting tool potential-features.md §2 describes, with no mutation surface.

## Scope

In:
- `PiholeClient.search_domain` wrapping `GET /api/search/{domain}` with `partial` and `N`.
- A new read-only route `GET /api/lookup?domain=…&partial=…` that fans out to every
  instance, returns each instance's matching allow/deny entries and gravity entries, and
  for exact lookups derives a per-instance verdict (`blocked` / `allowed` / `unlisted`)
  with a one-line reason, using FTL's own precedence order.
- A new **Lookup** nav page: domain input, partial-match toggle, one result card per
  instance showing the verdict and a table of the matching entries with group names.
- Docs: design.md §5.8, README compatibility paragraph, potential-features.md shipped list,
  the `pihole_client.py` endpoint inventory.

Out:
- Query-log search (`/api/queries`) and stats. That is the separate "cross-instance query
  log / stats dashboard" item in potential-features.md §2.
- Per-client evaluation ("would this be blocked for 192.168.1.50?"). The verdict considers
  every enabled entry regardless of group assignment; the page says so.
- Any mutation from the Lookup page (no "add to allowlist here" shortcut). Adding entries
  stays on the Domains page.
- CNAME-chain inspection. FTL's search endpoint does not follow CNAMEs and neither does
  this page.
- Caching or background refresh of results. One request per user-triggered search.

## Approach

**One endpoint, one fan-out, no new outbound pattern.** `/api/search/{domain}` is the
call behind `pihole -q`; it evaluates the exact term against the domain lists (including
regex rules, which it does execute) and gravity. It goes through the existing
`PiholeClient` with its pinned transport, so invariant #6 is inherited rather than
re-implemented. The route follows the `domains_list` fan-out shape: `_fanout` across
instances, per-instance errors collected into an `errors` map, never raised.

**Live-confirmed on lab-pihole, 2026-09-11 (read-only GETs, nothing written):**

- Response shape is `{"search": {"domains": [...], "gravity": [...], "results": {...},
  "parameters": {...}}, "took": float}`. `domains[]` items carry the same fields as
  `/api/domains` items (`domain`, `type` allow|deny, `kind` exact|regex, `comment`,
  `enabled`, `id`, `date_added`, `date_modified`, `groups`). `gravity[]` items carry
  `domain`, `address`, `type` allow|block, `comment`, `enabled`, `id`, `groups`, plus list
  metadata (`number`, `invalid_domains`, `abp_entries`, `status`, `date_*`) this plan
  ignores. `results` is `{"domains": {"exact", "regex"}, "gravity": {"allow", "block"},
  "total"}`.
- Regex rules are evaluated: `foo.mov` returned the deny regex `(\.|^)mov$` under
  `results.domains.regex`.
- Matching is case-insensitive: `DoubleClick.NET` matched the gravity entry
  `doubleclick.net`.
- An unlisted subdomain of a listed host (`zzz-notreal.doubleclick.net`) returns zero
  matches. That mirrors how plain hosts-format gravity entries actually block (exact host
  only), so an empty result is a truthful "no entry matches", not a false negative.
- `N` caps `domains` and `gravity` **separately**, not their combined length: `N=1` on a
  term with 2 domain matches and 12125 gravity matches returned 1 of each. `results` is
  never capped, and `results.total` always equals the four category counts summed, which
  is why the route compares it against the returned row count to set `truncated`.
- `N=1000` is accepted and returns the full small result set for an exact search.
- An empty term is a 400 from FTL. Odd characters (`foo bar`, `a/b`) are accepted when
  URL-quoted and simply match nothing.

**Verdict derivation.** The page's whole point is "why", so the route reduces the matches
to a verdict instead of leaving the user to rank them. The order is taken from FTL's
`check_domain_blocked` (`src/dnsmasq_interface.c`, master): exact allow → gravity allow
list (antigravity) → exact deny → regex allow → gravity block → regex deny. A regex allow
match sets the query's `allowed` flag and overrides gravity and regex deny; an exact deny
is evaluated before regex allow and wins over it. Only `enabled` entries count. Disabled
entries are still returned and shown, tagged as disabled, because "it's on the list but
switched off" is itself an answer.

**A verdict is given only when the answer is certain, and is `null` otherwise.** Two cases
produce `null`. A partial search gets none because substring hits say nothing about the
typed term. A truncated exact search gets none because `N` caps each array independently,
so an omitted entry could outrank every row that came back and flip the answer. Silence
beats a confident wrong answer here, since the verdict is the only thing this page really
sells. Truncation on an exact search should be vanishingly rare — the exact cap is 1000,
while a real exact result is bounded by the handful of list entries that match the term
plus one row per adlist carrying it — so this path is a guard, not an expected outcome.
`_verdict` is therefore only ever called with a complete result set.

**Disabled entries are assumed to appear in search results, unverified.** Every domain
entry and the single adlist on lab-pihole are enabled, so a read-only probe could not
observe FTL's behaviour for a disabled one, and this plan does not write to a live
instance to manufacture one. The design is safe either way. If FTL returns them,
`_verdict`'s enabled-filter is what keeps them from deciding the answer and the UI tags
them. If FTL filters them out first, the filter is a harmless no-op and the "Disabled"
tag never renders.

Group assignments are deliberately not evaluated. Doing it right means resolving the
client's groups and intersecting them with each entry's `groups`, which is per-client
evaluation (out of scope). The page shows each entry's group names and carries a
standing note that group-scoped entries may not apply to every client.

**UI placement.** A new nav item, not a search box on Domains or Block Lists. The answer
spans both of those screens plus gravity's contents, which neither shows. A modal from
Status was also considered and rejected: the result is a per-instance table with up to a
hundred rows, which is a page, not a popup.

**Partial matching** is included because `pihole -q --partial` is the common "what do my
lists say about doubleclick?" form, and it costs one boolean and one query parameter.
Partial searches routinely match thousands of gravity rows, so they cap at 100 and the UI
reports how many rows it is showing against the true total. Exact searches cap at 1000 for
the certainty reason above. Neither mode pages, because narrowing the term is the better
answer. Because `N` caps each array separately, a fully capped response holds up to twice
the cap in rows, so nothing in the UI may describe the row count as "the first {limit}".

Rejected alternatives:

- **Reuse `/api/domains` + `/api/lists` data client-side and match in the browser.**
  Rejected: gravity's contents are not exposed by `/api/lists` (only the subscribed
  addresses), so the browser cannot answer the gravity half at all, and it would
  re-implement regex evaluation that FTL already does.
- **Put the route in `app/routes/domains.py`.** Rejected: the result covers gravity and
  the verdict logic is its own concern; AGENTS.md's one-module-per-concern rule points to
  a new `lookup.py`, following `domains.py`'s module-local helper convention.
- **Compute the verdict in the browser.** Rejected: pure backend logic gets pytest
  coverage for free; the frontend has no test runner (potential-features.md §5).

## Files

- `app/pihole_client.py` — `GravityMatch`, `SearchCounts`, `SearchResult` dataclasses;
  `search_domain`; extract `get_domains`'s per-item parsing into a static `_parse_domain`
  so both readers share one validator; module docstring gains the confirmed
  `/api/search/{domain}` entry.
- `app/routes/lookup.py` — new; `LookupQuery` model, `_verdict`, `_lookup_instance`,
  `GET /api/lookup`. Copies `_fanout` / `_instance` / `_client` / `_api_error` the way
  `domains.py` does.
- `app/main.py` — `app.include_router(lookup.router)`.
- `frontend/src/api.ts` — `LookupDomainMatch`, `LookupGravityMatch`, `LookupCounts`,
  `LookupInstanceResult`, `LookupResponse` types and `api.lookupDomain`.
- `frontend/src/pages/LookupPage.tsx` — new; the search form and per-instance result cards.
- `frontend/src/App.tsx` — `lookup` nav entry with `FileSearchOutlined`, rendered between
  Domains and Block Lists.
- `tests/test_pihole_client.py` — `search_domain` request/parse tests.
- `tests/test_lookup.py` — new; route fan-out, validation, verdict, truncation tests.
- `docs/design.md` — new §5.8 "Domain lookup".
- `README.md` — a paragraph in "Pi-hole compatibility" for the Lookup page.
- `docs/potential-features.md` — move the §2 lookup bullet into the shipped list.
- `app/static/ui/` — rebuilt bundle (invariant #8).

## Steps

1. **`app/pihole_client.py` — dataclasses.** Next to `Domain`, add:

   ```python
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
   ```

2. **`app/pihole_client.py` — `_parse_domain`.** Move the body of `get_domains`'s loop
   (the `isinstance` checks through the `Domain(...)` construction) into
   `@staticmethod _parse_domain(item: Any) -> Domain`, raising the same
   `PiholeAPIError` messages it raises today. `get_domains` becomes
   `return [self._parse_domain(item) for item in raw_domains]`. Behaviour and messages
   unchanged; `test_domain_requests_use_type_kind_path_segments` still passes untouched.

3. **`app/pihole_client.py` — `search_domain`.**

   ```python
   def search_domain(self, term: str, *, partial: bool = False, limit: int = 20) -> SearchResult:
       path = f"/api/search/{quote(term, safe='')}"
       resp = self._client.get(
           path,
           params={"partial": "true" if partial else "false", "N": limit},
           headers=self._auth_headers(),
       )
       if resp.status_code != 200:
           raise PiholeAPIError(f"GET /api/search failed with HTTP {resp.status_code}")
       ...
   ```

   Parse `body["search"]`; require `domains` and `gravity` to be lists and `results` to
   be a dict, else `PiholeAPIError("Pi-hole search endpoint returned an invalid response")`.
   Each `domains[]` item goes through `_parse_domain`. Each `gravity[]` item requires
   `domain: str`, `address: str`, `type: str`, `enabled: bool`, and an int-list `groups`
   (same bool-excluding check `get_adlists` uses), else
   `PiholeAPIError("Pi-hole search endpoint returned an invalid gravity match")`.
   `SearchCounts` requires all five of `results.domains.exact`, `results.domains.regex`,
   `results.gravity.allow`, `results.gravity.block`, and `results.total` to be present and
   to be non-bool `int`s, else `PiholeAPIError("Pi-hole search endpoint returned invalid
   result counts")`. **Nothing here defaults to `0`.** `results.total` is the only evidence
   the route has that a result set is complete, so a missing or malformed value must become
   an instance error rather than a silent zero, which would read as "nothing was truncated"
   and hand a verdict to an incomplete result. Raising also matches how `get_adlists`,
   `get_domains`, and `get_groups` already treat malformed payloads. The docstring
   inventory gets:

   ```
   Domain lookup (confirmed 2026-09-11 against lab-pihole: shape, regex evaluation,
   case-insensitive match, and N-capping all verified read-only):
     GET /api/search/{domain}?partial=<bool>&N=<int>
   ```

4. **`app/routes/lookup.py` — module and query model.** New module, docstring
   `"""Read-only domain lookup: which list entries match a domain on each instance."""`.
   Copy `_fanout`, `_instance`, `_client`, `_api_error`, `credentials`, `router =
   APIRouter(prefix="/api")` from `domains.py` verbatim (no `_audit_landed_write`; nothing
   here writes). Constants `LOOKUP_LIMIT_EXACT = 1000` and `LOOKUP_LIMIT_PARTIAL = 100`,
   read through one helper so the worker and the route cannot disagree:

   ```python
   def _limit_for(partial: bool) -> int:
       return LOOKUP_LIMIT_PARTIAL if partial else LOOKUP_LIMIT_EXACT
   ```

   ```python
   class LookupQuery(BaseModel):
       domain: str = Field(min_length=1, max_length=253)
       partial: bool = False

       @field_validator("domain")
       @classmethod
       def validate_domain(cls, value: str) -> str:
           term = value.strip().lower().rstrip(".")
           if not term:
               raise ValueError("Enter a domain to look up")
           if any(ch.isspace() for ch in term) or "/" in term:
               raise ValueError("A lookup term cannot contain spaces or slashes")
           return term
   ```

   Hostname syntax is not enforced, because a partial term like `ad` is valid input.
   Lowercasing matches FTL's own case-insensitive behaviour and keeps the echoed `term`
   stable.

5. **`app/routes/lookup.py` — serializers and `_verdict`.**

   ```python
   def _domain_match(item: Domain) -> dict[str, Any]:
       return {"domain": item.domain, "type": item.type, "kind": item.kind,
               "comment": item.comment, "enabled": item.enabled, "groups": list(item.groups)}


   def _gravity_match(item: GravityMatch) -> dict[str, Any]:
       return {"domain": item.domain, "address": item.address, "type": item.type,
               "comment": item.comment, "enabled": item.enabled, "groups": list(item.groups)}


   def _verdict(result: SearchResult) -> tuple[Literal["blocked", "allowed", "unlisted"], str]:
       """FTL check_domain_blocked order; disabled entries never count.

       Callers must pass a complete result set. A capped result can omit a
       higher-precedence entry and invert the answer, so Step 6 withholds the
       verdict instead of calling this.
       """
       domains = [d for d in result.domains if d.enabled]
       gravity = [g for g in result.gravity if g.enabled]
       exact_allow = next((d for d in domains if d.type == "allow" and d.kind == "exact"), None)
       if exact_allow: return "allowed", f"Exact allow entry {exact_allow.domain}"
       gravity_allow = next((g for g in gravity if g.type == "allow"), None)
       if gravity_allow: return "allowed", f"Allow list {gravity_allow.address}"
       exact_deny = next((d for d in domains if d.type == "deny" and d.kind == "exact"), None)
       if exact_deny: return "blocked", f"Exact deny entry {exact_deny.domain}"
       regex_allow = next((d for d in domains if d.type == "allow" and d.kind == "regex"), None)
       if regex_allow: return "allowed", f"Regex allow entry {regex_allow.domain}"
       gravity_block = next((g for g in gravity if g.type == "block"), None)
       if gravity_block: return "blocked", f"Block list {gravity_block.address}"
       regex_deny = next((d for d in domains if d.type == "deny" and d.kind == "regex"), None)
       if regex_deny: return "blocked", f"Regex deny entry {regex_deny.domain}"
       return "unlisted", "No enabled entry matches this domain"
   ```

   Written as a flat sequence of `next(...)` checks so each precedence step is one
   readable line; the implementer may tidy the layout but must keep the order.

6. **`app/routes/lookup.py` — per-instance worker and route.**

   ```python
   def _lookup_instance(instance: Instance, query: LookupQuery) -> tuple[str, dict[str, Any] | None, str | None]:
       limit = _limit_for(query.partial)
       try:
           with _client(instance) as client:
               result = client.search_domain(query.domain, partial=query.partial, limit=limit)
       except HTTPException as exc:
           return instance.name, None, str(exc.detail)
       except PiholeAPIError as exc:
           return instance.name, None, str(exc)
       except Exception:  # noqa: BLE001 - transport details are intentionally hidden
           return instance.name, None, "Unable to reach Pi-hole instance"
       returned = len(result.domains) + len(result.gravity)
       truncated = result.counts.total > returned
       if query.partial or truncated:
           # A capped result can hide the entry that decides the answer.
           verdict, reason = None, None
       else:
           verdict, reason = _verdict(result)
       return instance.name, {
           "verdict": verdict,
           "reason": reason,
           "domains": [_domain_match(item) for item in result.domains],
           "gravity": [_gravity_match(item) for item in result.gravity],
           "counts": {"domains_exact": ..., "domains_regex": ..., "gravity_allow": ...,
                      "gravity_block": ..., "total": result.counts.total},
           "truncated": truncated,
       }, None


   @router.get("/lookup")
   def lookup(domain: str, partial: bool = False) -> dict[str, Any]:
       query = LookupQuery(domain=domain, partial=partial)   # ValidationError -> 422 via FastAPI
       instances = load_instances()
       results, errors = {}, {}
       for name, payload, error in _fanout(instances, lambda inst: _lookup_instance(inst, query)):
           if error is not None: errors[name] = error
           else: results[name] = payload
       return {"term": query.domain, "partial": query.partial, "limit": _limit_for(query.partial),
               "instance_names": [i.name for i in instances], "instances": results, "errors": errors}
   ```

   Wrap the `LookupQuery(...)` construction so a pydantic `ValidationError` becomes
   `HTTPException(422, detail=<first error message>)`; FastAPI does not auto-convert a
   model built inside the handler. No `record(...)` call anywhere in this module.

7. **`app/main.py`** — import `lookup` alongside the other route modules and add
   `app.include_router(lookup.router)` after `clients`.

8. **`frontend/src/api.ts` — types and client method.**

   ```ts
   export interface LookupDomainMatch {
     domain: string
     type: 'allow' | 'deny'
     kind: 'exact' | 'regex'
     comment: string
     enabled: boolean
     groups: number[]
   }

   export interface LookupGravityMatch {
     domain: string
     address: string
     type: 'allow' | 'block'
     comment: string
     enabled: boolean
     groups: number[]
   }

   export interface LookupCounts {
     domains_exact: number
     domains_regex: number
     gravity_allow: number
     gravity_block: number
     total: number
   }

   export interface LookupInstanceResult {
     verdict: 'blocked' | 'allowed' | 'unlisted' | null
     reason: string | null
     domains: LookupDomainMatch[]
     gravity: LookupGravityMatch[]
     counts: LookupCounts
     truncated: boolean
   }

   export interface LookupResponse {
     term: string
     partial: boolean
     limit: number
     instance_names: string[]
     instances: Record<string, LookupInstanceResult>
     errors: Record<string, string>
   }
   ```

   ```ts
   lookupDomain: (domain: string, partial: boolean, signal?: AbortSignal) =>
     request<LookupResponse>(
       `/api/lookup?domain=${encodeURIComponent(domain)}&partial=${partial}`,
       { signal },
     ),
   ```

9. **`frontend/src/pages/LookupPage.tsx`.** Functional component, no props. State:
   `term: string`, `partial: boolean`, `result?: LookupResponse`, `searching: boolean`,
   `groupRows: GroupRow[]`. On mount, fetch `api.listGroups()` once (with the same
   `cancelled` guard DomainsPage uses) and build the id→name map with a local
   `groupNameMap` identical to the one in `ClientsPage.tsx`. The search is user-triggered
   (Enter or the Search button), never on mount and never on a timer; it holds an
   `AbortController` in a ref and aborts any in-flight lookup before starting the next.
   Sequencing rule from ClientsPage applies: the groups fetch and the lookup are never
   awaited concurrently — the Search button is disabled until the groups fetch settles.

   Layout, top to bottom:
   - `page-heading` block: title "Lookup", secondary paragraph "Find which allow, deny, or
     block-list entries match a domain on each Pi-hole instance."
   - `Space.Compact` row: `Input` (placeholder `ads.example.com`, `allowClear`,
     `onPressEnter`), `Checkbox` "Partial match", primary `Button` with
     `SearchOutlined`, `loading={searching}`.
   - One `Alert` per entry in `result.errors` (`dns-error` class, `"<instance>
     unavailable"`), matching the other pages.
   - One `Card` per name in `result.instance_names` that has a result, titled with the
     instance name. The card `extra` keys off `verdict`, never off `partial`. When
     `verdict` is set it shows the `Tag` — `error` "Blocked", `success` "Allowed",
     `default` "Not listed" — followed by the reason as secondary text. When `verdict` is
     `null` it shows the counts instead ("3 domain entries · 122 block-list entries").
   - Inside each card, one `Table` (`pagination={false}`, `size="small"`, `scroll={{ x:
     'max-content' }}`) over the concatenated matches. Each row carries a `key` built when
     the two arrays are concatenated, using every field that distinguishes it:

     ```ts
     const key = `domain:${type}:${kind}:${domain}`    // domain-list rows
     const key = `gravity:${address}:${domain}`        // gravity rows
     ```

     Neither may collapse. `(domain, type, kind)` is Pi-hole's own primary key for
     domain-list entries, the same triple `_find_domain` already matches on, so an exact
     and a regex entry sharing a literal string stay distinct. A gravity row is one
     (list, domain) pair, so the address is what separates the common case of one domain
     appearing in several block lists — keying on source and domain alone would give
     those rows identical keys and let React reuse the wrong row.

     Columns: **Source** (`Tag` — "Allow list" / "Deny list" for
     domain entries, "Block list" / "Allow list (gravity)" for gravity entries, with the
     list address as secondary text below), **Entry** (`Typography.Text code` for regex
     kinds, plain for exact), **Kind** (exact / regex / gravity), **Enabled** (`Tag`
     success "Enabled" / default "Disabled"), **Groups** (name tags via the map, `#id`
     fallback), **Comment** (secondary).
   - Both messages below count `shown = result.domains.length + result.gravity.length`,
     the rows actually in the table. They must not use `limit`: `N` caps each array
     separately, so a capped result holds up to two `limit`s of rows and "the first 100"
     would be a visible lie above a 200-row table.
   - When `truncated`, a secondary line under the table reads "Showing {shown} of
     {counts.total} matches. Narrow the term to see the rest." For a truncated exact
     search, where the verdict was withheld, that line is an `Alert type="warning"`
     instead, reading "Too many matches to decide whether this domain is blocked. Showing
     {shown} of {counts.total}." The user must never be left to infer a verdict from a
     partial table.
   - A single `Alert type="info"` at the bottom of the results, shown once: "Verdicts
     consider every enabled entry. Entries scoped to specific groups may not apply to
     every client."
   - Empty states: before the first search, an `Empty` with "Enter a domain to look it up
     on every instance". After a search that yields no matches on any reachable instance,
     each card still renders with its "Not listed" verdict; no separate empty state.

   Split the per-instance card into a small `LookupInstanceCard` component in the same
   file (props: `name`, `result`, `partial`, `limit`, `groupNames: Record<number,
   string>`), keeping `LookupPage` itself to the form and loop.

10. **`frontend/src/App.tsx`** — import `LookupPage` and `FileSearchOutlined`; insert
    `{ key: 'lookup', icon: <FileSearchOutlined />, label: 'Lookup' }` between `domains`
    and `adlists`; add the `page === 'lookup' ? <LookupPage />` branch.

11. **Docs.** `docs/design.md`: add §5.8 "Domain lookup" after §5.7 — read-only, one
    fan-out per user-triggered search, verdict order as stated in Approach, group caveat,
    partial mode. `README.md` "Pi-hole compatibility": one paragraph saying the Lookup
    page calls Pi-hole's `/api/search` on every instance and derives a per-instance
    verdict. `docs/potential-features.md`: move the §2 bullet into the "Shipped" list as
    "plan 0013 (design.md §5.8)" and remove it from §2 and from "Suggested next pick".

12. **Build.** `cd frontend && npm run build && npm run lint`; commit `app/static/ui/`.

## Contracts

**New route** `GET /api/lookup?domain=<str>&partial=<bool>` — behind the auth middleware
(path is not in the public set). No request body. Query validation is `LookupQuery`
(Step 4); a failed validation returns 422 with a readable `detail` string.

Response (`200`) for `?domain=doubleclick.net`, built from that query's verbatim
lab-pihole result — no domain-list entry matches, one gravity block entry does, so the
verdict is `blocked` and `domains` is empty:

```json
{
  "term": "doubleclick.net",
  "partial": false,
  "limit": 1000,
  "instance_names": ["lab-pihole"],
  "instances": {
    "lab-pihole": {
      "verdict": "blocked",
      "reason": "Block list https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
      "domains": [],
      "gravity": [
        {"domain": "doubleclick.net", "address": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
         "type": "block", "comment": "Migrated from /etc/pihole/adlists.list", "enabled": true, "groups": [0]}
      ],
      "counts": {"domains_exact": 0, "domains_regex": 0, "gravity_allow": 0, "gravity_block": 1, "total": 1},
      "truncated": false
    }
  },
  "errors": {}
}
```

A second live case pins the precedence rule. `?domain=pubads.g.doubleclick.net` returns
the enabled exact allow entry `{"domain": "pubads.g.doubleclick.net", "type": "allow",
"kind": "exact", "comment": "Ads for PBS :(", "enabled": true, "groups": [0, 1]}` in
`domains` alongside the same gravity block row in `gravity`, with counts
`{"domains_exact": 1, "domains_regex": 0, "gravity_allow": 0, "gravity_block": 1,
"total": 2}`. Exact allow outranks gravity block, so the verdict is `allowed` with reason
`Exact allow entry pubads.g.doubleclick.net`.

`verdict` and `reason` are both `null` in exactly two cases — a partial search, and a
truncated exact search. `limit` echoes the cap actually used, 1000 for exact and 100 for
partial. An instance appears in exactly one of `instances` or `errors`. The `api.ts` side
is the `Lookup*` interfaces in Step 8, field for field.

**Pi-hole client** — `PiholeClient.search_domain(term, *, partial=False, limit=20) ->
SearchResult` (Steps 1–3). Outbound: `GET /api/search/{quoted term}?partial=…&N=…`.

No config keys, no DB or data-directory changes, no audit entries.

## Tests

- `tests/test_pihole_client.py::test_search_domain_quotes_term_and_parses_matches` —
  `MockTransport` asserts the request path is `/api/search/foo%2Fbar.example` with
  `partial=true` and `N=7`; a canned body in the confirmed shape parses into a
  `SearchResult` whose `domains[0]` is a `Domain` with the right type/kind/groups,
  `gravity[0]` is a `GravityMatch` with address/type/enabled/groups, and `counts.total`
  matches `results.total`.
- `tests/test_pihole_client.py::test_search_domain_rejects_invalid_response` — a 200
  body without `search`, and one whose `gravity` item lacks `address`, each raise
  `PiholeAPIError`; a non-200 status raises with the status in the message.
- `tests/test_pihole_client.py::test_search_domain_requires_complete_result_counts` —
  the regression guard for Revision 2(a). A 200 body with valid `domains` and `gravity`
  but a `results` that is missing `total`, has `total` as a string, or has `total` as a
  bool each raise `PiholeAPIError`, so no caller can mistake a malformed response for a
  complete one. A missing category count (`results.gravity.block`) raises too.
- The existing `tests/test_pihole_client.py::test_domain_requests_use_type_kind_path_segments`
  covers `get_domains` after the Step 2 extraction; it must stay green without edits.
- `tests/test_lookup.py::test_lookup_fans_out_and_isolates_instance_errors` — two
  instances; the fake client raises `PiholeAPIError("search failed")` for `pihole2`;
  `pihole1` lands in `instances` with a verdict, `pihole2` lands in `errors` with the
  message, `instance_names` lists both.
- `tests/test_lookup.py::test_lookup_unreachable_instance_hides_transport_detail` — a fake
  raising `httpx.ConnectError` yields `"Unable to reach Pi-hole instance"`.
- `tests/test_lookup.py::test_malformed_counts_become_an_instance_error_not_a_verdict` —
  the route-level half of Revision 2(a). A fake whose `search_domain` raises
  `PiholeAPIError` for bad counts puts that instance in `errors`, and it never appears in
  `instances` with a verdict.
- `tests/test_lookup.py::test_verdict_precedence` — parametrized over the six ordered
  cases, each pairing a higher-precedence entry with every lower one it must beat:
  exact allow beats gravity block and regex deny; gravity allow beats exact deny; exact
  deny beats regex allow; regex allow beats gravity block and regex deny; gravity block
  beats regex deny; regex deny alone blocks; no entries → `unlisted`.
- `tests/test_lookup.py::test_verdict_ignores_disabled_entries` — a disabled exact deny
  plus an enabled gravity block → `blocked` with the gravity reason; a disabled entry
  alone → `unlisted`, and the disabled entry is still present in `domains`.
- `tests/test_lookup.py::test_partial_lookup_has_no_verdict` — `partial=True` yields
  `verdict is None` and `reason is None`, and the fake client received `partial=True`.
- `tests/test_lookup.py::test_lookup_limit_depends_on_match_mode` — the fake records
  `limit == 1000` for an exact search and `limit == 100` for a partial one, and the
  response's `limit` field echoes each.
- `tests/test_lookup.py::test_lookup_flags_truncation` — a result whose `counts.total`
  exceeds the returned row count sets `truncated` true; equal counts set it false.
- `tests/test_lookup.py::test_truncated_exact_search_withholds_the_verdict` — the
  regression guard for Revision 1(a). An exact search whose returned rows are only a
  gravity block entry but whose `counts.total` is larger yields `verdict is None` and
  `reason is None` with `truncated` true, even though `_verdict` on those rows alone
  would say `blocked`. A second case with the same rows and matching counts yields
  `blocked`, proving the withholding is driven by truncation and nothing else.
- `tests/test_lookup.py::test_lookup_normalizes_and_validates_term` — `" Ads.Example. "`
  normalizes to `ads.example`; empty, whitespace-only, `a b`, `a/b`, and a 254-char term
  each produce a 422 `HTTPException`.
- `tests/test_lookup.py::test_lookup_module_has_no_audit_path` — asserts
  `not hasattr(lookup, "record")`, so a future edit cannot add a write path to this
  read-only module without tripping a test.

The fake client for `test_lookup.py` follows `FakeDomainsPiholeClient`'s shape: class-level
`results: dict[str, SearchResult]`, `fail: dict[str, Exception]`, and a `calls` list
capturing `(name, term, partial, limit)`.

## Invariants at risk

1. **One instance mutated at a time** — nothing is mutated. The fan-out is reads only,
   the same shape as `domains_list`.
2. **Diff before write** — no writes.
3. **Gravity gated behind real changes** — no gravity call; the route never touches
   `mark_gravity_pending`.
4. **No background scheduler** — searches run only on the user's submit. The page has no
   mount-time search and no timer; only the groups list loads on mount, as it does on
   Domains and Clients today.
5. **Credentials stay in the backend** — the response carries domains, addresses, and
   group ids only; `_client` resolves the password server-side as everywhere else.
6. **All outbound through `url_policy`** — `search_domain` uses the existing
   `PiholeClient` and its `_PinnedTransport`; no new `httpx` client and no user-supplied
   URL. The user's term is path-quoted with `quote(term, safe='')`, and `/` is rejected
   by validation before it gets there.
7. **Every route behind auth** — `/api/lookup` is not added to the public set in
   `auth_middleware.py`; a test in `tests/test_auth.py` is not needed because the middleware
   is prefix-based and unchanged.
8. **Compiled frontend committed** — Step 12.
9. **`main` is production** — work lands on `feature/0013-domain-lookup`.

## Acceptance criteria

- [ ] `GET /api/lookup?domain=doubleclick.net` against lab-pihole returns `blocked` with
      the StevenBlack address as the reason; `foo.mov` returns `blocked` with the regex
      deny reason; `pubads.g.doubleclick.net` returns `allowed` (exact allow beats the
      gravity block); `zzz-notreal.doubleclick.net` returns `unlisted`.
- [ ] `partial=true` for `doubleclick.net` returns `verdict: null`, `truncated: true`,
      `limit: 100`, and `counts.total` above 100. The exact searches above return
      `limit: 1000` and `truncated: false`.
- [ ] A truncated exact search returns `verdict: null` and the page shows the warning
      alert rather than a verdict tag. Exercised by the unit test; a live instance with
      1000+ matching entries is not required.
- [ ] A malformed `results` block from an instance puts that instance in `errors` with a
      readable message, and never yields a verdict.
- [ ] On a truncated partial search, the row count in the "Showing X of Y" line equals the
      number of rows actually in the table, checked against a capped result that fills both
      the domain and gravity arrays.
- [ ] Row keys are unique. A partial search whose gravity matches repeat one domain across
      lists renders every row with no duplicate-key warning in the browser console.

- [ ] Unauthenticated `GET /api/lookup?domain=x` is rejected by the auth middleware.
- [ ] `domain=` (empty) and `domain=a/b` return 422 with a readable message; the browser
      shows that message, not `[object Object]`.
- [ ] The Lookup page renders one card per instance with the verdict tag, the reason, the
      match table with group names, and the unavailable alert when an instance errors.
- [ ] Submitting a second search while one is in flight aborts the first (no stale
      result overwrites the newer one).
- [ ] No audit line is written by a lookup (`tail` the audit log across a search).
- [ ] `./.venv/bin/pytest` green, including the unchanged `get_domains` test.
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
- [ ] design.md §5.8, README, and potential-features.md updated as in Step 11.

Several acceptance checks need the keyring-backed credential for `lab-pihole`, which is
not reachable from Codex's sandbox ("No credential stored"), so they fall to the reviewer
running the app locally rather than to the implementer.

**Live verification, 2026-09-11.** Kyrnveil exercised the page manually against a real
instance and confirmed both verdict paths — a blocked host and an allowed host each
returned the expected answer. The remaining boxes above are still the reviewer's to run
under `/verify`.
