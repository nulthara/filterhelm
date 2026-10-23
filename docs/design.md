# FilterHelm — design and requirements

This document records what FilterHelm does, the safety rules it follows, and why. The
[README](../README.md) covers setup and security configuration. The
[deploy runbook](deploy.md) covers hosting.

---

## 1. Context

Many home and small-office networks run two or more Pi-hole instances so DNS keeps working
when one goes down. Those instances need the same local DNS records, block lists, and
allow/deny overrides.

Existing sync tools copy configuration from one instance to the others on a schedule. In
practice that can damage the gravity database and restart FTL, which takes a resolver down
for several minutes. Because every instance runs the same job at the same time, one bad
run can take DNS down for the whole network at once.

That failure mode shapes almost every rule in this document. FilterHelm is built for
looking at instances day to day and making occasional, deliberate changes by hand. It is
not a scheduled sync.

FilterHelm talks to each instance only through the Pi-hole v6 REST API. It needs no SSH
access and works the same for containerized and bare-metal installs.

---

## 2. Goals

1. **Compare local DNS across instances.** Flag a hostname that points at different
   addresses on different instances, or that exists on some instances but not others.
2. **Manage any number of instances.** Each has a name, URL, and credential, and a way to
   confirm the connection works.
3. **Manage block lists.** View, add, remove, enable, and disable them, and update gravity,
   on one instance or several.
4. **Manage a small set of settings.** View and edit them per instance without opening each
   instance's web UI.

## 3. Non-goals

- **Replacing Pi-hole's web UI.** FilterHelm compares and edits across instances. It is not
  a full admin console.
- **Background work.** FilterHelm has no daemon, scheduler, or timer. It acts only when
  someone uses it.
- **Public exposure.** It is meant for a private network, used by one operator or a small
  trusted group.
- **DHCP or router DNS.** FilterHelm reads and writes Pi-hole's own local DNS only.

---

## 4. Safety rules

These rules are the reason the project exists. Every feature follows them.

- **One instance at a time.** No operation writes to every instance at once. An
  "apply to several" action runs on each instance in turn and reports each result.
- **Gravity runs only on request.** An instance's gravity runs only when its block lists
  changed, and only through an explicit, visible action. Nothing triggers it implicitly or
  on a timer.
- **Diff before write.** Every change shows exactly what will change and asks for
  confirmation. Read-only comparison is the default mode.
- **No wholesale replace.** Changes add or remove the specific entries that differ. They
  never delete and recreate a whole list or record set.
- **v6 REST API only.** Each operation opens one session with the instance's app password
  and always closes it (`DELETE /api/auth`), including on error.

---

## 5. Functional requirements

### 5.1 Instance management

**Instances**

- Add an instance with a display name, base URL, and app password. The password goes to
  credential storage (§7.3), never to a plain config file.
- Test the connection before saving, using the same auth call the app uses at runtime.
  Report a clear pass or fail, not a generic network error.
- Edit and remove instances. Removing an instance only forgets it locally. It never touches
  the Pi-hole itself.
- Show each instance's live status (reachable, auth failed, or unreachable) wherever
  instances are listed.

**Health**

- The Health column reads `/api/info/{system,ftl,database,sensors}` in the same session as
  the connection check. It loads on open, on Refresh, and after Test, save, or remove.
- It shows host and FTL uptime, memory, swap, load, CPU, temperature, FTL process usage,
  and query database size. FTL uptime is converted from milliseconds to seconds.
- The server computes a status dot (healthy, warning, critical, or unavailable) from
  temperature against Pi-hole's `hot_limit`, RAM percent, swap percent, and 1-minute load
  per core.
- A failed health read leaves the instance reachable and marks Health unavailable. Sensors
  are optional.
- Details sit in a collapsible row under each instance. Warning and critical rows open
  automatically and healthy rows start collapsed. A manual expand or collapse sticks for the
  rest of the session.

**Backup**

- The Backup action fetches `GET /api/teleporter` through the backend in one session. The
  browser downloads it as `pihole-<name>-<UTC timestamp>.zip`.
- The archive stays in memory, capped at 64 MiB.
- Before sending it on, the backend checks every member's CRC, confirms `pihole.toml` and
  `gravity.db` are present, and confirms `pihole.toml` parses. A damaged or incomplete
  archive returns 502.
- Each download is audited as `backup_download` with its size and SHA-256. If the audit
  line cannot be written, the download fails with 503.
- Restore is not offered.

**Service actions**

- The Service menu offers Restart DNS, Flush logs, and Flush network table. Each calls one
  `POST /api/action/*` endpoint after confirmation and is audited as `ftl_<action>`.
- The audit status is `unconfirmed` when the request was sent but the transport failed,
  Pi-hole answered 5xx, or a 200 reply could not be read. A 5xx can mean a partial run,
  such as a flush that cleared the log but not the database.
- A 403 means the instance has `webserver.api.allow_destructive` turned off.
- Flush logs deletes the last 24 hours of query history.

**Blocking status**

- The Status page shows each instance's blocking state and any remaining pause time.
- Blocking can be paused for 10 seconds, 30 seconds, 5 minutes, or indefinitely, and
  resumed at any time, on one instance or a selected group.
- Group changes run one instance at a time and report partial failures.

### 5.2 Local DNS records

- Read local A/AAAA records (`dns.hosts`) and CNAME records (`dns.cnameRecords`) from every
  instance. CNAMEs have their own tab on the Local DNS page.
- Show one row per hostname across all instances, not one table per instance, so
  differences are visible at a glance.
- Flag two kinds of difference:
  - **Conflict.** The same hostname has different targets on different instances.
  - **Missing.** The hostname exists on some instances but not this one.
- Copy a record from one instance to another as a single, confirmed write.
- Add, edit, and delete records on one selected instance.
- Select several records and remove them with one confirmation. Removal runs one instance
  at a time.
- Writes use item-level `dns.hosts` operations, so each change touches one record. Each
  CNAME entry holds one alias, so a CNAME delete never rewrites a surviving line.
- Filter the table by hostname, address, or instance name. Filtering never changes the
  conflict or missing flags.

### 5.3 Block lists

- Show every subscribed list in one table, with which instances have it, its enabled state,
  and Pi-hole's own update metadata. A missing list is as visible as a missing DNS record.
- Add, remove, enable, or disable a list on one instance, or apply the same change to
  several instances in turn.
- Select several lists and enable, disable, or remove them with one confirmation.
- Assign a list to groups per instance with Edit groups (§5.6).
- Any list change, including a group change, marks that instance's gravity as pending.
- **Update gravity** is a separate button, enabled only while the instance has pending
  changes. No other action triggers it. A failed update leaves the instance pending.

### 5.4 Settings

- Manage a deliberately small set of settings:
  - upstream DNS servers (`dns.upstreams`)
  - query logging (`dns.queryLogging`)
  - privacy level (`misc.privacylevel`)
  - conditional forwarding (`dns.revServers`)
- View and edit settings one instance at a time. Nothing assumes settings should match
  across instances, because some, such as upstream resolvers, are legitimately
  host-specific.
- Every change shows a diff before it is applied.
- Apply refuses with 409 when the instance’s values no longer match the reviewed diff.

### 5.5 Domain allow/deny overrides

- List individual allow and deny entries (exact or regex) with their enabled state. These
  are separate from subscribed block lists (§5.3).
- Show which overrides exist on which instances, in the same shape as §5.2 and §5.3.
- Add, remove, enable, or disable an override on one instance, or add the same override to
  several instances in turn.
- Select several overrides and enable, disable, or remove them with one confirmation.
- Assign an override to groups per instance with Edit groups (§5.6).
- Override changes, including group changes, take effect without a gravity run. This was
  confirmed against a live instance.

### 5.6 Groups

- Show every group across instances, flagging groups that are missing or whose enabled
  state differs.
- Add a group to one or more instances in turn. Enable, disable, or delete a group on one
  instance at a time.
- Deleting a group first previews the block lists and domains assigned only to that group.
  The operator chooses to leave them unassigned, delete them, or move them to Default.
- The server recomputes that set at write time and handles those entries before deleting
  the group. If any step fails, the group stays and the page shows per-entry results.
- If handling those entries changes a block list, that instance's gravity becomes pending.
- Deleting the Default group (id 0) requires an explicit risk acknowledgment and cannot
  move entries to Default.
- Group changes themselves do not trigger gravity.

### 5.7 Clients

- Show every configured client across instances, with add and delete actions and a
  per-instance editor for group membership.
- The browser resolves group IDs to names per instance. Group IDs are local to each
  instance and are never compared across instances.
- Client changes do not trigger gravity.

### 5.8 Domain lookup

- The Lookup page calls Pi-hole's `/api/search` on every instance, once per search the
  operator submits. It never searches on page load, on a timer, or from cache, and never
  writes.
- An exact search derives a verdict from enabled matches in FTL's precedence order:
  1. exact allow
  2. gravity allow (antigravity)
  3. exact deny
  4. regex allow
  5. gravity block
  6. regex deny
- No enabled match means "Not listed". Disabled matches are shown but never decide the
  verdict.
- A partial search finds substring matches and shows counts, not a verdict.
- Exact searches cap each result list at 1000 entries and partial searches at 100. A
  truncated exact search withholds its verdict and shows a warning with the shown and total
  counts.
- Results show matching entries, list addresses, enabled state, comments, and group names.
  Entries scoped to specific groups may not apply to every client.
- Per-client evaluation and CNAME-chain evaluation are out of scope.

### 5.9 Query log and statistics

**Loading**

- The Queries page is read-only. It loads on open and on Apply or Refresh, with no timer,
  no cross-instance paging, and no audit entry.
- Each instance uses one session and reads, in order: summary, permitted and blocked top
  domains, permitted and blocked top clients, upstreams, and the query log.
- Instances are queried in parallel. A failed read marks that instance unavailable.

**Query log**

- Rows from every instance merge newest first.
- The time window is 60–86400 seconds (default 3600). The per-instance row limit is 1–500
  (default 100).
- When results are truncated, the page shows matching counts and how far back FTL's
  in-memory history goes. Narrowing the window or filters shows the rest. FilterHelm never
  reads FTL's on-disk history.

**Filters**

- Status is either one FTL status or "Blocked (any reason)". Blocked uses FTL's own
  twelve-status `is_blocked()` set, including `DBBUSY`.
- FTL accepts one status per request, so for "Blocked" the server reads each blocked status,
  sums the counts, merges newest first, and caps the result at the per-instance limit.
- Domain and client filters accept FTL wildcards and reject whitespace and slashes. Domains
  are lowercased.
- A client pattern made only of hex digits, `.`, `:`, and `*`, with at least one dot or
  colon, is sent as `client_ip`. Any other pattern is sent as `client_name`. A hex-only
  hostname such as `cafe.dead` is therefore treated as an IP pattern.

**Statistics**

- Statistics always cover FTL's last 24 hours, regardless of log filters and window.
- Summary cards show per-instance counters and combined totals for the instances that
  answered.
- Top domains and clients show ten per instance. There is no combined ranking, because
  truncated lists cannot be summed accurately.
- Upstreams keep the `cache` and `blocklist` pseudo-entries (`port: -1`), shown without an
  address or response time. Real upstream response times are shown in milliseconds.
- Switching tabs makes no new request.

### 5.10 Drift

**Scope**

- The read-only Drift page compares local DNS A/AAAA records, CNAMEs, block lists, domains,
  groups, clients, and the four managed settings in one `GET /api/drift` request.
- Each instance uses one session with five reads in order. DNS, CNAMEs, and settings share
  one config fetch.
- Refresh is explicit. The page never polls, writes, or reconciles.

**Display**

- Category cards show drifting and total counts and filter the table by category.
- State (All, Missing, Differs) and text filters narrow the rows. Each reachable instance
  gets its own value column.
- Only drifting entries are sent to the browser, capped at 200 per category. Counts stay
  exact, and capped categories are labeled.
- Each row links to the page that owns it. CNAME rows open the CNAME tab.

**Comparison**

- States match the owning pages, and a conflict outranks a missing entry.
- DNS compares addresses, and CNAMEs compare target and TTL.
- Block lists, domains, and groups compare presence and enabled state. Clients compare
  presence only.
- Block lists compare raw addresses. Comments and group assignments are not compared.
- Settings compare raw values, including list order. Unset values show "(not set)" and
  empty ones "(empty)".

**Reachability**

- An instance must answer all five reads to take part. Any failed read excludes it and
  shows an error.
- "Missing" always means absent on a reachable instance, never unknown on an unreachable
  one.
- Fewer than two reachable instances produces a warning. Conflicts within a single
  instance still appear.

### 5.11 Audit log viewer

- The Audit log page reads `GET /api/audit-log` behind normal authentication. Viewing the
  log does not add to it.
- Instance, action, and actor filters match exactly on the server. Text search is
  case-insensitive and matches values and nested keys as written, including backslashes.
  Filter dropdowns list every value in the whole file.
- The server scans the whole file and returns matches newest first. The default limit is
  200 and the maximum 1000. The `matched` count includes matches beyond the limit.
- Malformed or partial lines are skipped and counted, and the page warns when that happens.
  Missing fields get safe defaults.
- Rows expand to show before and after values.
- Results load from filters or an explicit Refresh. The page never polls, writes, or
  exports.

---

## 6. Non-functional requirements

- **Private network only.** A local run binds to loopback. A hosted run sits behind an HTTPS
  reverse proxy on a private network.
- **Credentials stay out of plain config.** Instance passwords live in the OS keyring, or a
  `0600` file when no keyring exists (§7.3). They never reach the browser.
- **No background activity.** No scheduler and no timer that writes. Page reads are fine.
  Writes happen only on an explicit action.
- **Traceable.** Every change is audited with the instance, what changed, the old and new
  values, and a timestamp. Sync tools fail silently, so a change history is worth its cost.
- **Low upkeep.** FilterHelm is a tool people open occasionally, not a service that must
  stay up. Weigh every new dependency against that.
- **Tolerates a down instance.** If one instance is unreachable, pages show the rest plus a
  clear per-instance error, instead of failing outright.

---

## 7. Architecture

### 7.1 Stack

FilterHelm is a small FastAPI backend with a React, TypeScript, and Ant Design frontend.
FastAPI serves the pre-built frontend, so the app runs as one service and deployment needs
no Node.js.

- **Python backend.** One runtime to maintain, and the Pi-hole API calls are simple HTTP.
- **Browser UI.** Avoids Electron or Tauri packaging and update work for a low-upkeep tool.
- **Ant Design.** Provides consistent tables, forms, validation, feedback, and design
  tokens for the table-heavy pages in §5.
- **Frontend independence.** The safety rules in §4 live in the backend. They would hold
  the same with any frontend.

A native desktop toolkit such as PySide6 was considered. It would give a real window and a
one-click launch, but it adds packaging work. The choice is reversible.

### 7.2 Overview

```
┌──────────────────┐        ┌──────────────────────────┐
│  Browser (UI)    │◄──────►│  FilterHelm (FastAPI)    │
│  React + AntD    │  /api  │  - instance registry     │
└──────────────────┘        │  - diff and drift logic  │
                            │  - credential store      │
                            │  - audit log             │
                            └────────────┬─────────────┘
                                         │ Pi-hole v6 REST API
                                         │ one session per operation
                  ┌──────────────────────┼──────────────────────┐
                  ▼                      ▼                      ▼
           ┌─────────────┐        ┌─────────────┐        ┌─────────────┐
           │  pihole-a   │        │  pihole-b   │        │  pihole-N   │
           └─────────────┘        └─────────────┘        └─────────────┘
```

### 7.3 Data and credential storage

All state lives in one data directory. It defaults to `~/.config/filterhelm/` and can be
changed with `FILTERHELM_DATA`.

| File | Contents |
| --- | --- |
| `instances.yml` | Instance names, URLs, and other non-secret metadata |
| `secrets.yml` | Instance passwords, only when no OS keyring is available (mode `0600`) |
| `pending-gravity.yml` | Instances with block-list changes awaiting a gravity update |
| `auth.db` | FilterHelm user accounts and sessions |
| `audit.log` | Change history (mode `0600`) |

FilterHelm does not cache Pi-hole data. The instances themselves are the source of truth
for DNS records, lists, and settings, and every page reads them live.

On a headless host there is no keyring, so the `secrets.yml` fallback is what runs. That
file is protected by its permissions, not encrypted.

---

## 8. UI layout

A left nav lists every page, and a single content pane shows the selected page:

- Status
- Queries
- Drift
- Instances
- Local DNS
- Domains
- Lookup
- Block Lists
- Groups
- Clients
- Settings
- Audit log

The cross-instance pages share one shape, with a row per entry and a column per instance:

```
  LOCAL DNS                                          [ + Add record ]
  ┌──────────────────────┬────────────────┬────────────────┬──────────┐
  │ Hostname             │ pihole-a       │ pihole-b       │          │
  ├──────────────────────┼────────────────┼────────────────┼──────────┤
  │ router.example.com   │ 192.168.1.1  ✓ │ 192.168.1.1  ✓ │          │
  │ switch.example.com   │ 192.168.1.18 ✓ │ — missing      │ [Copy →] │
  │ nas.example.com      │ 192.168.1.40 ⚠ │ 192.168.1.41 ⚠ │          │
  └──────────────────────┴────────────────┴────────────────┴──────────┘
  ⚠ conflicting target     — missing on that instance
```

```
  BLOCK LISTS                                  [ + Add list ]
  ┌──────────────────────────────┬────────────┬────────────┐
  │ List URL                     │ pihole-a   │ pihole-b   │
  ├──────────────────────────────┼────────────┼────────────┤
  │ StevenBlack/hosts            │ ● on       │ ● on       │
  │ example/extra-list           │ ● on       │ — missing  │ [Copy to pihole-b]
  └──────────────────────────────┴────────────┴────────────┘
  pihole-a: up to date     pihole-b: [ Update gravity ] (1 pending change)
```

Settings are per instance, so that page has an instance picker instead of instance
columns:

```
  SETTINGS — pihole-b ▾                              [ Review changes ]
  ┌──────────────────────────────────────────────────────────────┐
  │ Upstream DNS servers     [ 9.9.9.9, 149.112.112.112       ]  │
  │ Conditional forwarding   [ true,192.168.1.0/24,192.168.1.1 ] │
  │ Query logging            [x] enabled                         │
  │ Privacy level            [ 0 - show everything ▾ ]           │
  └──────────────────────────────────────────────────────────────┘
```

Every button that changes something opens a confirmation showing the exact diff first.
That step cannot be skipped. Bulk actions list every affected entry and instance before
they run.

---

## 9. Deployment

FilterHelm ships as a container image with the committed frontend and hash-pinned Python
dependencies. The reference deployment runs it as a non-root container under a rootful
Podman Quadlet, which generates `filterhelm.service`:

```ini
[Container]
Image=localhost/filterhelm:latest
ContainerName=filterhelm
Pull=never
Network=host
Volume=/var/lib/filterhelm:/data
EnvironmentFile=/etc/filterhelm/env
Environment=FILTERHELM_DATA=/data
ReadOnly=true
NoNewPrivileges=true
DropCapability=ALL
```

- The container listens on `0.0.0.0:8420`. A reverse proxy on the private network handles
  TLS and is the only client that should reach it.
- `Network=host` keeps that listening setup and preserves real client addresses.
- The app writes only to `/data`. The rest of the container filesystem is read-only.

[deploy.md](deploy.md) covers bootstrap, cutover, and rollback.

---

## 10. Open items

**Features**

- **Unreachable DNS targets.** Flag local records whose target address does not answer.
  This would be informational only and never block a change.
- **Accepted differences.** Let the operator mark a difference as intentional so it stops
  showing as drift.
- **Settings scope.** The four managed settings (§5.4) are a starting set. Add more only
  when there is a clear need.
- **Router DNS.** Possibly manage local DNS on routers that have their own API, such as
  OPNsense. The open question is whether a router becomes another kind of instance, which
  would reuse the diff, confirm, and audit logic, or stays a separate tool that keeps
  FilterHelm's reach limited to DNS resolvers.

**Safety and testing**

- **Credential fallback.** Evaluate an encrypted or mounted-secret alternative to the
  plaintext `0600` `secrets.yml`.
- **Mutation safety follow-ups.** Detect env-forced settings.
- **Known gaps.**
  - Multi-instance list and domain adds give no per-instance recovery signal if the client
    times out mid-loop.
  - List and domain updates read the entry, then write it back whole. That can overwrite a
    comment or group change someone made in Pi-hole's own UI in between.
  - Fixing either likely means idempotent adds or optimistic concurrency on
    `date_modified`, applied to both features together.

**Project**

- A versioned release archive alongside the container image.
- A contribution guide, security policy, screenshots, issue templates, upgrade notes, and
  backup instructions.
