# Pi-hole Manager — design, requirements & layout

**Moved here 2026-08-30** from a private infrastructure repo, where it was drafted — a
standalone app is better off self-documenting in its own repo than relying
on a companion doc (and a chat transcript) living somewhere else. A short
pointer stub was left at the old location in that repo instead of a dead link.

*A note on cross-references below:* double-bracket wiki links and `.md` names
in this document refer to internal notes kept outside this repo.

A local GUI application for managing both piholes, requested directly rather
than growing out of internal notes. Related but not redundant — see §9
for how the two fit together.

---

## 1. Context

Two resolvers today:

| Name | Address | Host |
|---|---|---|
| pihole1 | 192.168.1.2 | Docker container on a macvlan network |
| pihole2 | 192.168.1.3 | Container on a separate host, normal Debian |

Keeping them consistent (adlists, local DNS records, allow/deny) has already
cost one failed attempt: **nebula-sync was tried and rejected** — it caused
gravity DB damage and FTL restarts, which take a resolver down for 5–10
minutes. Because it ran both instances on the same schedule, a bad run
degraded DNS for the whole house at once. That failure shapes almost every
constraint below.

Internal notes describe a CLI/automation answer to the same problem
(nothing built yet). This document is a second, GUI-shaped answer — for
day-to-day looking and occasional hand-driven changes, not scheduled
automation. Both share the same underlying integration point: the **Pi-hole
v6 REST API**, confirmed as the right surface in that design (it sidesteps
pihole1 not being an SSH host, and is what nebula-sync itself used).

---

## 2. Goals

1. **See local DNS entries across both piholes and flag inconsistencies** —
   same hostname pointing at different IPs, an entry present on one resolver
   and missing on the other, orphaned or unreachable targets.
2. **Support adding multiple Pi-hole instances**, not hardcoded to two —
   name, URL, credential, with a way to confirm the connection works.
3. **Manage adlists** — view, add, remove, enable/disable, and trigger a
   gravity update, per instance or across instances at once.
4. **Manage settings** — view and edit a defined subset of each instance's
   configuration through the same GUI, rather than the web UI per host.

## 3. Non-goals (for v1)

- Not a replacement for either pihole's own web UI — this is a
  cross-instance comparison and bulk-editing tool, not a full admin console.
- Not a daemon and not scheduled. It runs when Kyrnveil opens it and stops
  doing anything the moment it's closed. (An optional read-only scheduled
  drift check is the internal notes' `--check --diff` idea, not this app's
  job — see §9.)
- Not multi-user, not exposed off the local machine. Single operator, local
  network only.
- DHCP-side records (OPNsense) are out of scope — reads Pi-hole's own local
  DNS only. Whether Pi-hole local records should even overlap with
  DHCP-issued ones is the same open question internal notes flagged and
  is still unresolved.

---

## 4. Constraints carried over from the nebula-sync failure

These aren't optional hardening — they're the reason this project is worth
building carefully rather than reaching for another off-the-shelf syncer:

- **One instance mutated at a time, always.** Never a fan-out write to all
  configured piholes in a single operation. A "apply to all" action is
  sequential with per-instance results shown, not a single atomic broadcast.
- **Gravity runs only when adlists actually changed on that instance**, and
  only as an explicit, visible action — never implicit, never on a timer.
- **Diff before write, every time.** Any mutating action shows what will
  change and requires confirmation. Read-only comparison is the default
  mode of the app, not a special flag.
- **No wholesale replace.** Sync operations add/remove the specific
  differing entries, never delete-and-recreate a list or record set.
- **v6 REST API only**, session-based auth (app password), always releasing
  the session (`DELETE /api/auth`) when done or on error.

---

## 5. Functional requirements

### 5.1 Instance management
- Add an instance: display name, base URL, app password (or API token,
  whichever v6 exposes) — stored via credential storage in §7.3, never in
  plain config.
- Test-connection action before saving, using the same auth call the app
  will use at runtime — surface a clear pass/fail rather than a generic
  network error.
- Edit and remove instances. Removing an instance only forgets it locally;
  it never touches the pihole itself.
- Per-instance live status: reachable / auth failed / unreachable, shown
  wherever instances are listed (not just on an instances screen).
- The Instances Health column reads host and FTL uptime, memory, swap, load, CPU,
  temperature, FTL process usage, and query-database size from
  `/api/info/{system,ftl,database,sensors}` inside the connection-check session,
  on open, Refresh, and after Test, save, and remove via the existing list reload.
  FTL uptime is converted from milliseconds to seconds. A failed health read leaves
  the instance reachable with Health marked unavailable; sensors are optional.
  The row shows a status dot (healthy / warning / critical / unavailable) computed
  server-side from temperature against Pi-hole's `hot_limit`, RAM percent, swap
  percent, and 1-minute load per core with fixed thresholds. The numbers sit in a
  collapsible detail row under each instance. A warning or critical instance opens
  its detail row automatically; a healthy one starts collapsed. Expanding or
  collapsing a row overrides that default for the rest of the session.
- Each Instances row has a Backup action. It fetches `GET /api/teleporter` through
  the backend in one session and downloads the zip as `pihole-<name>-<UTC timestamp>.zip`.
  The archive passes through memory only, capped at 64 MiB. Before delivery the backend
  verifies every member's CRC and confirms that `pihole.toml` and `gravity.db` are present
  and that `pihole.toml` parses, so a damaged or incomplete archive returns 502.
  Each download is audited as `backup_download` with size and SHA-256, and fails with
  503 if the audit line cannot be written. Restore is not offered.
- A dedicated status view shows the current blocking state and remaining
  pause timer. Blocking can be paused for 10 seconds, 30 seconds, 5 minutes,
  or indefinitely and resumed for one instance or a selected group. Group
  mutations remain sequential and surface partial failures.

### 5.2 Local DNS records + inconsistency detection
- Multi-select A/AAAA and CNAME records to remove them with a single confirm, applied sequentially
  one instance at a time.
- Pull local A/CNAME records from every configured instance.
- CNAMEs (`dns.cnameRecords`) share the Local DNS screen as a second tab, with the same
  missing/conflict states. One alias per entry, so unlike `dns.hosts` a delete never has
  to rewrite a surviving line.
- Present a unified, per-hostname view across all instances, not a separate
  table per pihole — that's what makes inconsistencies visible at a glance
  rather than requiring a mental diff.
- Flag, distinctly:
  - **Conflicting IP** — same hostname, different target IP on two or more
    instances.
  - **Missing** — hostname exists on N-1 instances but not this one.
  - **Orphaned target** *(best-effort)* — record points at an IP that
    doesn't answer/ping, informational only, never blocks anything.
- Per-inconsistency actions: copy a record from instance A onto instance B
  (single-record write, diffed and confirmed per §4), or mark the divergence
  as intentional/ignored so it stops flagging.
- Manual add/edit/delete of a local record, against one selected instance.

### 5.3 Adlist management
- Multi-select adlists to enable, disable, or remove them with a single confirm, applied
  sequentially one instance at a time.
- Per instance: list configured adlists, each with its enabled/disabled
  state and last-update status (Pi-hole's own list metadata).
- Cross-instance view mirroring §5.2's shape: which adlists exist on which
  instances, so a missing list is as visible as a missing DNS record.
- Add / remove / enable / disable an adlist on a selected instance (or
  apply the same change across instances, sequentially per §4).
- **Update gravity** as its own explicit button, enabled only when that
  instance has a pending adlist change — never triggered as a side effect
  of any other action.
- An adlist can be assigned to one or more groups per instance via 'Edit groups'
  (§5.6); changing group membership marks that instance's gravity pending, same as
  any other adlist edit.

### 5.4 Settings management
- Scope this to a defined, deliberately small subset of the v6 settings API
  to start — candidates: upstream DNS servers, conditional forwarding,
  query logging on/off, privacy level. Expand later; don't try to mirror
  the entire Pi-hole settings surface in v1.
- Settings are viewed and edited **per instance** — nothing here assumes
  they should match across resolvers. Pi-hole2's Unbound-recursive wishlist
  item (internal notes) is a good example of a setting that's legitimately
  host-specific.
- Same diff-before-write rule as everything else.

### 5.5 Domain allow/deny management
- Multi-select domain overrides to enable, disable, or remove them with a single confirm,
  applied sequentially one instance at a time.
- Per instance: list individual allow/deny domain entries (exact hostname or
  regex), each with its enabled/disabled state — distinct from subscribed adlists
  (§5.3).
- Cross-instance view mirroring §5.2/§5.3's shape: which domain overrides exist on
  which instances.
- Add / remove / enable / disable a domain override on a selected instance (or
  apply the same addition across instances, sequentially per §4).
- No gravity step — domain overrides take effect without a gravity run, unlike
  adlists (§5.3), confirmed against a live instance before this feature merged.
- A domain override can be assigned to groups per instance via 'Edit groups' (§5.6);
  confirmed against a live instance before merge that this does not require a gravity
  run (see plan 0007's Acceptance criteria).

### 5.6 Group management
- Cross-instance list of groups, including missing and enabled-state conflict status.
- Add a group to one or more selected instances sequentially; enable, disable, or
  delete a group on one selected instance at a time.
- Deleting a group previews the block lists and domains scoped solely to it and offers
  to leave them unassigned, delete them, or reassign them to Default. The orphan set is
  recomputed server-side at write time; orphan handling runs first, and any failure leaves
  the group intact with per-entry results. A cascade changing an adlist marks gravity
  pending for that instance. Deleting Default (id 0) requires explicit risk acknowledgment
  and cannot use reassign.
- Group changes themselves do not trigger gravity, pending live verification before merge that
  enabled-state changes take effect without a gravity run.

### 5.7 Client management
- Cross-instance list of configured clients, with add and delete actions and a
  per-instance editor for group membership.
- Group membership is resolved to names in the browser from the groups endpoint;
  group IDs are instance-local and are never compared across Pi-hole instances.
- Client membership changes do not trigger gravity, pending the same live
  no-gravity verification required for group management.


### 5.8 Domain lookup
- Read-only Lookup page searches Pi-hole's `/api/search` on every instance, with one
  fan-out per user-triggered search. No mount-time search, timer, caching, or writes.
- Exact searches derive a verdict from enabled matches in FTL precedence order:
  exact allow → gravity allow (antigravity) → exact deny → regex allow → gravity
  block → regex deny. No enabled match means "Not listed". Disabled matches are
  displayed but never decide the verdict.
- Partial matching searches for substring hits and gives counts instead of a verdict.
  Exact searches cap each result array at 1000 entries; partial searches cap each at
  100. A truncated exact search also withholds its verdict and shows a warning.
  Truncation messages report the actual displayed row count against the total.
- Results show matching entries, list addresses, enabled state, comments, and
  instance-local group names. Verdicts consider every enabled entry; entries scoped
  to specific groups may not apply to every client. Per-client and CNAME-chain
  evaluation are outside this feature's scope.

### 5.9 Query log and statistics

- Read-only Queries page makes one fan-out on open and on Apply or Refresh.
  There is no timer, paging across instances, audit entry, or mutation. Each instance
  uses one session, reading the summary, permitted and blocked top domains, permitted
  and blocked top clients, upstreams, then the query log sequentially. Instances fan
  out concurrently; a failed read makes that instance unavailable for the response.
- The log merges rows newest-first across instances. Its window is bounded to
  60–86400 seconds (default 3600), and its per-instance limit to 1–500 (default 100).
  Matching counts and the start of in-memory history explain truncated results;
  narrow the window or filters to see the rest. No on-disk history is read.
- Status may be one FTL status or "Blocked (any reason)". Blocked follows FTL's
  twelve-status `is_blocked()` set, including `DBBUSY`. FTL accepts one status per
  request, so the server reads each blocked status, sums matching counts, merges
  newest-first, and caps the result again at the per-instance limit.
- Domain and client filters accept FTL wildcards; whitespace and slashes are rejected.
  Domains are lowercased. A client pattern containing only hex digits, `.`, `:`, and
  `*`, with at least one dot or colon, goes to `client_ip`; other nonempty patterns
  go to `client_name`. Only one is sent. Hex-only hostnames such as `cafe.dead`
  consequently classify as IP patterns.
- Statistics always cover FTL's last 24 hours, independent of log filters and window.
  Summary cards show per-instance counters and combined totals for reporting instances.
  Permitted and blocked top domains and clients are fixed at ten per instance; there
  is no combined ranking because truncated lists cannot be summed accurately.
- Upstreams retain the `cache` and `blocklist` pseudo-entries (`port: -1`), displayed
  without an address or response time. Real upstream response statistics are displayed
  in milliseconds. Switching among the four tabs makes no further request.

### 5.10 Drift dashboard

The read-only Drift screen aggregates Local DNS A/AAAA, CNAME records, block-list
definitions, domains, groups, clients, and the four managed settings in one
`GET /api/drift` request. Each instance uses one session with five sequential
reads; DNS, CNAMEs, and settings share a single config-tree fetch.

Category cards show drifting / total counts and filter the table by category.
State (All / Missing / Differs) and text filters narrow the rows, with one value
column per reachable instance. Only drifting entries cross the wire, capped at
200 per category; counts remain exact and capped categories are identified.
Refresh is explicit, with no polling, mutation, or reconciliation; each row links to its
owning page, with CNAME rows opening the CNAME records tab.

States match the owning pages: conflict outranks missing; DNS compares addresses,
CNAMEs compare target and TTL, block lists/domains/groups compare presence and
enabled state, and clients compare presence only. Block lists retain raw address
keys. Comments and group assignments are not compared. Settings compare raw
values, including list order; display strings are presentation only. Unset values
show “(not set)” and empty displays show “(empty)”.

An instance must answer all five reads to participate. Any failed read excludes
its entire snapshot and shows an error. Missing means absent on a reachable
instance, never unknown on an unreachable one. Fewer than two reachable
instances produces a warning; single-instance self-conflicts still appear.

### 5.11 Audit log viewer

- The Audit log page provides read-only `GET /api/audit-log` access behind the
  existing authentication middleware. Viewing creates no audit entry.
- Instance, action, and actor filters match exactly on the server. Case-insensitive
  text search matches values and nested keys, including literal backslashes,
  rather than JSON-escaped text. Dropdowns use facets from the entire file.
- The server scans the whole file and returns matching entries newest first in
  append order, with a default limit of 200 and maximum of 1000. The exact
  `matched` count includes matches beyond the returned limit.
- Malformed or partial lines are skipped and counted; the page warns when lines
  were skipped. Missing fields receive safe defaults.
- Rows expand to show Before and After values. Filters and an explicit Refresh
  button load results; there is no polling, mutation, or log export.

---

## 6. Non-functional requirements

- **Local only.** Binds to localhost/loopback if it runs as a local web
  server (see §7.1) — never listens on the LAN interface.
- **Credentials never in plain files committed anywhere.** Stored via OS
  keyring or an encrypted local secrets file — see §7.3.
- **No background activity.** No scheduler, no polling timer that mutates
  state; reads for the dashboard are fine, writes only ever happen on an
  explicit user action.
- **Traceable.** Every mutating action (record write, adlist change,
  gravity trigger, setting change) is logged locally with instance, what
  changed, old value → new value, and timestamp. Given nebula-sync's
  silent damage, an audit trail here is cheap insurance, not gold-plating.
- **Low upkeep.** Should not need its own patching/monitoring/maintenance
  routine to stay usable — it's a tool Kyrnveil runs occasionally, not a
  service that needs to stay up. Weigh any dependency addition against that.
- **Resilient to one instance being down.** If pihole1 or pihole2 is
  unreachable, the app degrades to showing the others plus a clear
  per-instance error, rather than failing the whole view.

---

## 7. Proposed architecture

### 7.1 Tech stack

**Implemented direction: a small FastAPI backend plus a React/TypeScript
frontend using Ant Design, run with one command as a browser-based UI.** The
production frontend is pre-built and served by FastAPI, so this still runs as
a single service and does not require Node.js on the deployment target.

Rationale:
- Matches the stack used by the other small Python services this runs
  alongside — one less language/runtime to maintain.
- Avoids Electron/Tauri packaging and update overhead for what is, per §6, a
  low-upkeep tool.
- Ant Design supplies consistent enterprise UI components, validation,
  feedback, and design tokens for the table/form-heavy workflows in §5.
- The v6 REST API calls this app makes are the same shape whether the UI is
  a browser tab or a native window — nothing about the safety model in §4
  depends on the frontend choice.

Alternative considered: a native desktop toolkit (e.g. PySide6). Viable if a
"real window, not a browser tab" matters more than initial simplicity — worth
revisiting if the local-web approach turns out annoying to launch day to day
(e.g. wanting a taskbar icon / single-click launch rather than a terminal
command). Flagging as a reversible decision, not a hard commitment.

### 7.2 High-level architecture

```
┌─────────────────────┐        ┌──────────────────────────┐
│   Browser (UI)       │◄──────►│  Local app server         │
│  127.0.0.1:PORT       │  HTTP  │  (FastAPI/Flask)          │
└─────────────────────┘        │  - instance registry       │
                                │  - diff engine             │
                                │  - credential store        │
                                │  - audit log               │
                                └────────────┬─────────────┘
                                             │ Pi-hole v6 REST API
                                             │ (session auth per instance)
                        ┌────────────────────┼────────────────────┐
                        ▼                    ▼                    ▼
                 ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
                 │  pihole1     │      │  pihole2     │      │  (future)    │
                 │ .1.2         │      │ .1.3         │      │  instance N  │
                 └─────────────┘      └─────────────┘      └─────────────┘
```

### 7.3 Data & credential storage

- Local config directory, e.g. `~/.config/pihole-manager/`:
  - `instances.yml` — non-secret metadata: name, base URL, notes. Mirrors the
    shape of the internal notes' `hosts.ini`, deliberately, since both tools
    describe the same instances.
  - Credentials **not** in that file. Use the OS keyring (Python `keyring`
    package) keyed by instance name; falls back to a `chmod 600` local secrets
    file only if no keyring backend is available.
  - `audit.log` (or a small local SQLite/JSON file) for the change history
    from §6.
- No server-side database needed beyond that — instance lists and settings
  are small, and the source of truth for DNS/adlist/settings data is always
  the piholes themselves, fetched live rather than cached long-term.

---

## 8. Proposed UI layout

Left nav, single content pane — five screens plus an instance-add modal.

```
┌───────────────────────────────────────────────────────────────────┐
│  Pi-hole Manager                                    [+ Add instance]│
├───────────┬───────────────────────────────────────────────────────┤
│ Dashboard │  DASHBOARD                                             │
│ DNS       │  ┌───────────────┐  ┌───────────────┐                  │
│ Adlists   │  │ pihole1        │  │ pihole2        │                 │
│ Settings  │  │ ● reachable    │  │ ● reachable    │                 │
│ Activity  │  │ 3 DNS drifts   │  │ 3 DNS drifts   │                 │
│           │  │ 1 adlist diff  │  │ 1 adlist diff  │                 │
│           │  └───────────────┘  └───────────────┘                  │
│           │                                                        │
│           │  Recent activity                                       │
│           │  08-29 14:02  pihole1  local record added  (you)       │
│           │  08-28 09:15  pihole2  adlist disabled      (you)       │
└───────────┴───────────────────────────────────────────────────────┘
```

```
  DNS RECORDS                                    [ + Add record ]
  ┌───────────────────────────┬──────────────┬──────────────┬───────┐
  │ Hostname                  │ pihole1       │ pihole2       │       │
  ├───────────────────────────┼──────────────┼──────────────┼───────┤
  │ router.example.com        │ .1.2   ✓     │ .1.2   ✓     │       │
  │ switch.example.com        │ .1.18   ✓    │ — missing    │ [Copy →]│
  │ nas.example.com           │ .1.40   ⚠    │ .1.41   ⚠    │ [Resolve]│
  └───────────────────────────┴──────────────┴──────────────┴───────┘
  ⚠ = conflicting IP        — = missing on that instance
```

```
  ADLISTS                                    [ + Add adlist to... ]
  ┌───────────────────────────────────────┬──────────┬──────────┐
  │ List URL                               │ pihole1   │ pihole2   │
  ├───────────────────────────────────────┼──────────┼──────────┤
  │ StevenBlack/hosts                       │ ● on      │ ● on      │
  │ someone/extra-list                      │ ● on      │ — absent  │ [Add to pihole2]
  └───────────────────────────────────────┴──────────┴──────────┘
  pihole1: gravity up to date     pihole2: [ Update gravity ] (1 pending change)
```

```
  SETTINGS — pihole2 ▾                                    [ Save changes ]
  ┌─────────────────────────────────────────────────────────────────┐
  │ Upstream DNS servers        [ 9.9.9.9, 149.112.112.112       ]   │
  │ Conditional forwarding      [x] enabled   192.168.1.1   / .home  │
  │ Query logging                [x] enabled                        │
  │ Privacy level                 [ 0 - show everything ▾ ]          │
  └─────────────────────────────────────────────────────────────────┘
  Instance picker at top switches context — settings are per-instance,
  not synced, per §5.4.
```

```
  ADD INSTANCE (modal)
  ┌───────────────────────────────────────┐
  │ Name          [ pihole3            ]   │
  │ Base URL      [ http://192.168.1.x ]   │
  │ App password  [ ••••••••••••••••   ]   │
  │                                        │
  │        [ Test connection ]  [ Save ]   │
  └───────────────────────────────────────┘
```

Every mutating button (`Copy →`, `Update gravity`, `Save changes`, adlist
add/remove) opens a confirm step showing the exact diff before it fires —
per §4, that step is not optional or skippable, including bulk actions, which show every
affected record and instance before firing.

---

## 9. Open questions

- ~~**Where does the code live?**~~ — **decided: `~/git/pihole-manager`, its
  own Gitea repo.** **Updated 2026-08-30: this design doc moved here too**
  (`docs/design.md`), rather than staying behind in a private infrastructure repo — a standalone app
  is better off self-documenting in its own repo than relying on a companion
  doc (and a chat transcript) living somewhere else. See §12.
- **DHCP-adjacent records** — partially answered: **there is currently no
  overlap** — OPNsense manages zero local DNS entries today, so Pi-hole is
  the sole source for all of them. That was worth confirming explicitly
  rather than assumed. What's still open is the roadmap item this surfaced —
  see §13.
- **Settings scope** — §5.4's subset list is a starting guess; confirm which
  settings actually matter before building the screen.
- **Local-web vs. native window** — §7.1 recommends local-web; revisit if
  daily launch friction makes that annoying in practice.

## 10. MVP scope

**Built so far** (commit `11a2ed5`):
- §5.1: add/edit/remove instance, credential storage (keyring with a
  root-owned-file fallback — see §12's note on why the fallback path is the
  one that actually matters once this runs on a headless LXC), test-connection,
  live blocking status, and confirmed individual/group pause and resume.
- §5.4: view/edit a defined field subset (upstream DNS, query logging,
  privacy level, conditional forwarding), diff-before-write confirm screen,
  audit log entry per change.
- §5.2: unified hostname view across instances, missing/conflict indicators,
  and confirmed per-instance add/edit/delete using item-level `dns.hosts`
  API operations.
- §5.3: unified subscribed block-list view, per-instance add/copy,
  enable/disable/remove actions, persistent pending-gravity state, and a
  separate confirmed gravity update.
- Smoke-tested end to end against a throwaway fake Pi-hole v6 API server,
  including catching a real bug before it shipped: an unchecked HTML
  checkbox submits nothing at all, which the first pass mistook for "field
  untouched" instead of "turned off" — fixed so absence of a boolean field
  on this form always means `False`.
- **Still unverified**: `app/pihole_client.py`'s actual endpoint paths and
  payload shapes against a real pihole1/pihole2, per that file's own
  docstring warning. Confirming that is the next real step, not further
  scaffolding.

---

## 11. Repo and hosting

**Hosting: a dedicated deploy host.** Putting the app behind the reverse
proxy does not require putting it on the proxy host. Keeping the app on a
separate host preserves the proxy's dedicated role and isolates the app's
resource needs. The deploy host was stood up on 2026-08-30; reverse-proxy
configuration is pending its IP being on hand.

**Access control matters for a credential-mutating tool.** This app can
rewrite DNS records and flip adlists off. In addition to §6's local/LAN
restriction, add a reverse-proxy **Access List** (HTTP basic auth): this
tool deserves more access friction than a read-only dashboard.

**Deploy mechanism: Gitea Actions, worked out in `docs/deploy.md`.** The
runner lives directly on the deploy target, scoped to this repo. A deploy
is a local `git reset --hard` + service restart, with no SSH keys or
cross-host access needed for the deployment steps. See the runbook for
setup details, still unexecuted as of this writing.

Deployment shape: a non-root container managed by a rootful Podman Quadlet.
The image uses the committed frontend bundle and hash-pinned Python dependencies.
The Quadlet generates `pihole-manager.service`; see [deploy.md](deploy.md) for
bootstrap, cutover, and rollback instructions.

```ini
[Container]
Image=localhost/pihole-manager:latest
ContainerName=pihole-manager
Pull=never
Network=host
Volume=/var/lib/pihole-manager:/data
EnvironmentFile=/etc/pihole-manager/env
ReadOnly=true
NoNewPrivileges=true
DropCapability=ALL
```

Note the bind address flips from `127.0.0.1` (§7's original workstation
assumption) to `0.0.0.0` here: this is a hosted service that other LAN
devices reach through the reverse proxy. The proxy handles LAN-facing
exposure and access control, while the app binds its host's interface.
`Network=host` preserves this listen posture and real client addresses.

**Credential storage on this host:** `app/config.py`'s `CredentialStore`
already handles this — it tries the OS keyring first and falls back to a
`chmod 600` YAML file at `/var/lib/pihole-manager/secrets.yml` on the host
(`/data/secrets.yml` in the container) when no keyring backend exists, which is
the expected case on a headless LXC. That fallback path, not the keyring path,
is what actually runs once this is hosted rather than run from a developer's
desktop session.

---

## 12. Long-term: OPNsense-managed local DNS too

**TODO:** Add support for maintaining local DNS entries in OPNsense.

Flagged as a decent long-term addition: extend this app (or its scope) to
also manage OPNsense's local DNS entries, once the Pi-hole side is solid.
Two things worth deciding before starting that, not before finishing §5.1/§5.4:

- **Does it become a third kind of "instance"** in this same app (OPNsense
  has its own REST API), or a separate concern entirely? The former reuses
  the diff/confirm/audit machinery already built for Pi-hole; the latter
  keeps this app's blast radius to DNS resolvers only, which the nebula-sync
  history (§4) is a reasonable argument for keeping narrow.
- **What changes about the "no current overlap" fact above** the moment
  OPNsense records exist too — that's the same overlap question §10 flagged,
  just now with an actual second party instead of a hypothetical one.

Not scoped further than that here — genuinely a "later" item, not this
quarter's.

---

## 13. UI follow-ups

- **Done — searchable Local DNS:** the unified table filters client-side by
  hostname, IP address, or instance name without modifying records or their
  missing/conflict indicators.

---

## 14. Public-release readiness

- **Partially done — privacy scrub:** plan 0016 scrubbed the working tree,
  guarded by `tests/test_repo_hygiene.py`. Git history still carries the
  original values and must be handled at publish time by exporting a fresh
  history or running `git filter-repo`.
- **TODO — licensing:** choose and add an open-source license.
- **Done — access control:** local Argon2id administrator authentication with
  opaque server-side sessions is the default; deployments may explicitly use
  trusted reverse-proxy identity-header mode instead. The modes never fall
  back to one another.
- **Done — request integrity:** same-origin `Origin`/`Referer` and Fetch
  Metadata checks protect every mutating method, including compatibility
  Jinja forms. Reverse proxies must preserve the public `Host` header.
- **Done — legacy cleanup:** the superseded Jinja instance/settings routes,
  templates, static CSS, and form-only dependencies were removed after the
  React UI reached feature parity. Settings validation now lives in a
  route-independent module used by the JSON API.
- **Done — outbound request policy:** instance URLs use centralized
  origin-only HTTP(S) validation, prohibit embedded credentials and
  redirects, allow RFC1918/IPv6 ULA destinations by default, and require an
  exact-hostname, exact-IP, or CIDR allowlist for public destinations. The
  baseline pre-connect DNS check and its DNS-rebinding/TOCTOU limitation are
  documented in the README. New block-list URLs receive syntax validation and
  canonicalization only: HTTP(S) is required, embedded credentials, control
  characters, fragments, and prohibited literal-IP hosts are rejected. They are
  not resolved or subject to `PIHOLE_MANAGER_PUBLIC_ALLOWLIST`, because Pi-hole
  fetches them. Update, delete, and bulk inputs identify existing lists without
  URL screening.
- **TODO — secrets:** document the `0600` YAML fallback as protected plaintext
  and evaluate an encrypted or mounted-secret alternative.
- **Done — compatibility:** FTL 6.x is the declared supported API family.
  The Instances UI shows server-reported Core, Web, FTL, and Docker versions,
  compatibility status, and available updates; future majors are marked
  untested rather than assumed compatible.
- **Done — HTTP hardening:** bounded request bodies, process-local sliding
  window rate limits, an explicit inbound trusted-host list, browser security
  headers, and sanitized upstream errors are enforced. Environment controls
  and the multi-worker/reverse-proxy rate-limit limitations are documented in
  the README.
- **Done — local privacy:** `audit.log` is forced to mode `0600`; documentation
  identifies the usernames, client addresses, DNS data, and setting values it
  retains and explicitly excludes passwords and session tokens.
- **Done — reproducibility:** exact direct requirements are declared in
  `pyproject.toml`, with the complete Linux production closure recorded in
  `requirements.lock`.
- **TODO — safety tests:** cover partial mutation failures and rollback,
  concurrent drift, multi-host records, IPv6, environment-locked settings,
  and partially reachable instance sets. Concrete known instance (flagged by
  a Codex adversarial review of plan 0003, accepted as shipped rather than
  blocking that plan): `adlist_add`/`domain_add`'s sequential multi-instance
  loops give no per-instance recovery signal on a client-side timeout
  mid-loop, and `update_adlist`/`update_domain`'s fetch-then-full-PUT can
  silently clobber a concurrent comment/group edit made outside this app
  (e.g. via Pi-hole's own web UI). A fix would likely mean either an
  idempotent/no-op-on-identical-state add, or optimistic concurrency via
  `date_modified`/`date_updated`, applied to both features together.
- **Distribution:** the container image and Compose example ship with the committed
  frontend assets. A versioned release archive is still open.
- **TODO — project operations:** add contribution and security policies,
  screenshots, issue templates, automated checks, upgrade notes, and backup
  instructions.
