# 0026 — FTL service actions (restart DNS, flush logs, flush network table)

Status: in-review           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0026-ftl-service-actions
Author: Claude (architect)
Date: 2026-10-03

## Problem

Restarting a Pi-hole's DNS resolver, flushing its logs, or clearing its network table
currently means opening that instance's own web UI or shelling into it. Pi-hole FTL v6
exposes all three as `POST /api/action/*`. This app already holds the credentials and
already wraps the sibling `/api/action/gravity`. The app should offer the three actions
per instance, each confirmed by the user and recorded in the audit log, as
`docs/potential-features.md` §3 suggests.

## Scope

In:
- `PiholeClient.run_action()` covering `POST /api/action/restartdns`,
  `/api/action/flush/logs`, and `/api/action/flush/network`.
- One backend route, `POST /api/instances/{name}/actions/{action}`, for a single instance.
- Audit entries `ftl_restart_dns`, `ftl_flush_logs`, `ftl_flush_network`.
- A per-row "Service" dropdown on the Instances page. Each item opens a confirm modal
  that describes the consequence.
- Doc updates in `docs/design.md` §5.1, `README.md` (audit sentence), and
  `docs/potential-features.md`.

Out:
- Multi-instance or "all instances" actions. Every call targets exactly one instance.
- `/api/action/flush/arp`. It is an alias that runs the same FTL handler as
  `/flush/network`.
- Reboot and power-off. They are not part of the FTL v6 action API.
- A preview read before the action (for example, counting the queries a flush would
  delete). See Approach.
- Changes to the gravity flow or to `update_gravity()`.

## Approach

FTL facts this plan depends on, checked against `pi-hole/FTL` master
(`src/api/action.c`, `src/api/api.c`, `src/files.c`, `src/database/network-table.c`):

| Endpoint | Effect | Success body | Refusal |
| --- | --- | --- | --- |
| `POST /api/action/restartdns` | Sets FTL's exit code to restart. FTL sends its reply first, then exits and comes back. | `{"status":"success"}` | 403 if `webserver.api.allow_destructive` is false |
| `POST /api/action/flush/logs` | Truncates `pihole.log`, clears FTL's in-memory queries, and deletes the last 24 h of queries from the on-disk database | `{"status":"success"}`; 500 on failure | same 403 |
| `POST /api/action/flush/network` | `DELETE FROM network_addresses; DELETE FROM network;` in `pihole-FTL.db`. The `client` and `group` tables in `gravity.db` are untouched. | `{"status":"success"}`; 500 on failure | same 403 |

The design decisions:

1. **One client method with a fixed path map.** `run_action(action)` looks the path up
   in a module constant keyed by the `FtlAction` literal. No caller can build an
   arbitrary `/api/action/...` path. Three near-identical methods were rejected because
   they would triplicate the status and body checks.
2. **One route with a `Literal` path parameter.** FastAPI rejects unknown actions with
   422 before the handler runs. Three routes were rejected for the same duplication reason.
3. **The confirm modal is the gate. There is no diff.** These actions have no target state
   to compare against, so invariant #2 does not apply in its usual form (see Invariants).
   A preview read was considered and rejected. A query count for flush-logs would need
   one extra round trip per click, and flush-network would need a new network-table read
   this app has no other use for. The modal copy states each consequence instead,
   including that two of the actions cannot be undone.
4. **Any outcome that may have changed the instance gets audited.** Three outcomes after
   the POST leave the action possibly applied, and each is audited as
   `{"status": "unconfirmed", "reason": ...}`:
   - **Transport error** (connection reset, read timeout). Restart is the likely case,
     because FTL exits right after replying. The route returns 503.
   - **HTTP 5xx.** FTL can fail partway through. `flush_dnsmasq_log()` truncates
     `pihole.log` and clears in-memory queries, then deletes from the database and
     returns 500 if that delete fails. `flush_network_table()` runs two separate
     `DELETE`s. The route returns 502.
   - **HTTP 200 with an unreadable or non-success body.** The handler ran, so the action
     most likely landed. The route returns 502.

   Definite refusals are not audited. FTL checks `allow_destructive` before it acts, so a
   403 means nothing ran. Any other 4xx (for example a 401 from an expired session) is
   rejected before the handler. Failures before the POST (credential missing, auth
   failure, connect refused) are not audited either, because nothing was sent.
   *Revision 2:* the first version audited only transport errors. An adversarial review
   showed that a partial `flush_logs` returning 500 erased data with no audit line.
5. **Audit after a landed action never fails the request.** The route uses the existing
   `_audit_landed_write` helper, the same as the bulk routes. Backup's fail-closed
   pattern does not fit here. Backup can withhold the archive when the audit fails, but
   an action that has already landed cannot be undone.
6. **Logout after restart may fail, and that is fine.** `_logout` already swallows
   `httpx.HTTPError` and ignores the status code, so a restarting FTL does not turn a
   success into an error. No change to `_logout`.
7. **403 gets a readable message.** The client does not echo Pi-hole's response body
   (`test_upstream_error_does_not_include_response_body`), so 403 maps to a fixed message
   that names the `webserver.api.allow_destructive` setting.

## Files

- `app/pihole_client.py` — add `FtlAction` literal, `_FTL_ACTION_PATHS`, `PiholeActionUnconfirmedError`, `PiholeClient.run_action()`.
- `app/routes/api.py` — add `FtlActionResult` model and `instance_action` route.
- `frontend/src/api.ts` — add `FtlAction`, `FtlActionResult`, `api.runFtlAction()`.
- `frontend/src/pages/InstancesPage.tsx` — add "Service" dropdown, confirm modals, running state.
- `app/static/ui/` — rebuilt frontend output.
- `tests/test_pihole_client.py` — client tests for `run_action`.
- `tests/test_api.py` — route tests; add `run_action` to `FakePiholeClient`.
- `docs/design.md` — §5.1 bullet describing the actions.
- `README.md` — add FTL service actions to the audit-log sentence.
- `docs/potential-features.md` — move "FTL service actions" from §3 to "Shipped" and
  update "Suggested next pick".

## Steps

1. **Client (`app/pihole_client.py`).**
   - Add near the other module constants:
     ```python
     FtlAction = Literal["restart_dns", "flush_logs", "flush_network"]

     _FTL_ACTION_PATHS: dict[str, str] = {
         "restart_dns": "/api/action/restartdns",
         "flush_logs": "/api/action/flush/logs",
         "flush_network": "/api/action/flush/network",
     }
     ```
     Import `Literal` from `typing` if it is not already imported.
   - Add `PiholeClient.run_action(self, action: FtlAction) -> None`, placed after
     `update_gravity()`:
     - `path = _FTL_ACTION_PATHS[action]`; `resp = self._client.post(path, headers=self._auth_headers())`.
       Send no body. Gravity already posts with no body, and FTL accepts it.
     - `403` → `PiholeAPIError("Pi-hole refused the action (HTTP 403); check webserver.api.allow_destructive on the instance", status_code=403)`.
     - Any other 4xx → `PiholeAPIError(f"POST {path} failed with HTTP {resp.status_code}", status_code=resp.status_code)`.
     - Any status `>= 500` → `PiholeActionUnconfirmedError(f"POST {path} failed with HTTP {resp.status_code}", status_code=resp.status_code)`.
     - Any other non-200 status (1xx or 3xx) → plain `PiholeAPIError`, as for 4xx.
     - `200` whose body does not parse as JSON, is not a dict, or has
       `status != "success"` → `PiholeActionUnconfirmedError("Pi-hole action endpoint returned an invalid response", status_code=200)`.
     - Let `httpx.HTTPError` propagate unchanged. The route distinguishes it.
   - Add next to `PiholeAPIError`:
     ```python
     class PiholeActionUnconfirmedError(PiholeAPIError):
         """Pi-hole received the action but did not confirm success; it may have run."""
     ```
     It subclasses `PiholeAPIError`, so callers that catch the base class keep working.

2. **Route (`app/routes/api.py`).**
   - Import `FtlAction` from `app.pihole_client`.
   - Add the model:
     ```python
     class FtlActionResult(BaseModel):
         instance: str
         action: FtlAction
     ```
   - Add the route after `instance_backup`:
     ```python
     @router.post("/instances/{name}/actions/{action}", response_model=FtlActionResult)
     def instance_action(name: str, action: FtlAction):
     ```
     Behaviour, in order:
     1. `instance = _instance(name)` (404 if unknown).
     2. Open `with _client(instance) as client:`. A missing credential gives 409 from `_client`.
        An auth or connect failure in `__enter__` maps through `_api_error`. In both cases
        nothing is audited.
     3. Inside the `with`, call `client.run_action(action)` in its own `try`:
        - `httpx.HTTPError` → call `_audit_landed_write(name, f"ftl_{action}", {}, {"status": "unconfirmed", "reason": "transport_error"})`,
          then raise `HTTPException(503, detail="The action was sent but Pi-hole did not confirm it; it may have run. Check the instance.")`.
        - `PiholeActionUnconfirmedError` → call `_audit_landed_write(name, f"ftl_{action}", {}, {"status": "unconfirmed", "reason": reason})`.
          `reason` is `f"http_{exc.status_code}"` for a 5xx and `"invalid_response"` for a
          200. Then raise `HTTPException(502, detail=f"{exc}; the action may have partly run. Check the instance.")`.
          Catch this before any `PiholeAPIError` handling, because it is a subclass.
        - Plain `PiholeAPIError` propagates to the outer handler → `_api_error` → 502 with
          the client's message. Nothing is audited.
        - Audit `reason` values are fixed strings. Never put `str(exc)` from an httpx error
          into the audit line.
     4. On success, after the `with` block exits (logout done or swallowed), call
        `_audit_landed_write(name, f"ftl_{action}", {}, {"status": "success"})` and return
        `{"instance": name, "action": action}`.
     - Outer error handling follows `instance_backup`: re-raise `HTTPException`, and map any
       other exception through `_api_error`.

3. **Frontend API (`frontend/src/api.ts`).**
   - Add the types and method shown under Contracts. Place `runFtlAction` next to
     `downloadBackup`.

4. **Instances page (`frontend/src/pages/InstancesPage.tsx`).**
   - `const { message, modal } = App.useApp()`.
   - New state: `const [actionRunning, setActionRunning] = useState<string>()`, holding the
     instance name.
   - A module-level `const ftlActions: Record<FtlAction, { label: string; title: (name: string) => string; content: (name: string) => string; okText: string; danger: boolean; done: (name: string) => string }>`
     with this copy:

     | action | label | title | content | okText | danger | done |
     | --- | --- | --- | --- | --- | --- | --- |
     | `restart_dns` | Restart DNS | `Restart DNS on ${name}?` | `FTL restarts and ${name} stops answering DNS queries for a few seconds. Clients that use only this resolver will see failed lookups until it is back.` | Restart DNS | false | `DNS restart requested on ${name}` |
     | `flush_logs` | Flush logs | `Flush logs on ${name}?` | `This empties pihole.log and deletes the last 24 hours of query history from ${name}'s memory and database. The Queries page and 24-hour counters for ${name} start again from zero. This cannot be undone.` | Flush logs | true | `Logs flushed on ${name}` |
     | `flush_network` | Flush network table | `Flush the network table on ${name}?` | `This clears ${name}'s list of seen devices (MAC, IP, hostname). Pi-hole rebuilds it as devices make queries. Client and group assignments are kept. This cannot be undone.` | Flush network table | true | `Network table flushed on ${name}` |

   - Handler `runAction(name: string, action: FtlAction)`:
     `await modal.confirm({ title, content, okText, okButtonProps: danger ? { danger: true } : undefined })`.
     Return if not confirmed. Otherwise `setActionRunning(name)`, `await api.runFtlAction(name, action)`,
     `message.success(done(name))`. On error, show `message.error(error instanceof Error ? error.message : 'Action failed')`.
     In `finally`, call `setActionRunning(undefined)`. Do not reload the instance list after
     the action.
   - In the Actions column, add an AntD `Dropdown` between Backup and Edit with
     `menu={{ items, onClick: ({ key }) => void runAction(item.name, key as FtlAction) }}`. The
     items are built from `ftlActions`, with `danger: true` on the two flush items. The
     trigger is `<Button icon={<ToolOutlined />} loading={actionRunning === item.name} disabled={actionRunning !== undefined && actionRunning !== item.name}>Service <DownOutlined /></Button>`.
     Every row's dropdown is disabled while any action runs, so the UI never has two
     actions in flight.

5. **Build.** `cd frontend && npm run build && npm run lint`, then commit `app/static/ui/`.

6. **Docs.**
   - `docs/design.md` §5.1: add a bullet after the Backup bullet. It says that each
     Instances row has a Service menu with Restart DNS, Flush logs, and Flush network
     table. Each action calls one `POST /api/action/*` endpoint on that instance after a
     confirm. Each is audited as `ftl_<action>`, and as `unconfirmed` when the transport
     fails after the request was sent, when Pi-hole answers 5xx, or when a 200 reply
     is unreadable. A 5xx can mean a partial run, for example a flush that cleared the
     log but not the database. A 403 means the instance has
     `webserver.api.allow_destructive` off. Flush logs deletes the last 24 h of query
     history.
   - `README.md`: in the sentence listing what the audit log records, add "FTL service
     actions" after "backup downloads".
   - `docs/potential-features.md`: remove the FTL bullet from §3, add a "Shipped" entry
     for plan 0026, and change "Suggested next pick" to name the `_adlist_key`
     injectivity bug in §6. That bug is a correctness problem on `main` today.

## Contracts

Backend:

```python
# app/pihole_client.py
FtlAction = Literal["restart_dns", "flush_logs", "flush_network"]

# app/routes/api.py
class FtlActionResult(BaseModel):
    instance: str
    action: FtlAction

@router.post("/instances/{name}/actions/{action}", response_model=FtlActionResult)
def instance_action(name: str, action: FtlAction): ...
```

| Case | Status | Body |
| --- | --- | --- |
| success | 200 | `{"instance": "<name>", "action": "<action>"}` |
| unknown action | 422 | FastAPI validation error |
| unknown instance | 404 | `{"detail": "Instance not found"}` |
| no credential | 409 | `{"detail": "No credential stored"}` |
| Pi-hole 403 | 502 | `{"detail": "Pi-hole refused the action (HTTP 403); check webserver.api.allow_destructive on the instance"}` |
| other Pi-hole 4xx | 502 | `{"detail": "POST /api/action/... failed with HTTP <code>"}` |
| Pi-hole 5xx (audited `unconfirmed`, `reason: "http_<code>"`) | 502 | `{"detail": "POST /api/action/... failed with HTTP <code>; the action may have partly run. Check the instance."}` |
| Pi-hole 200 with bad body (audited `unconfirmed`, `reason: "invalid_response"`) | 502 | `{"detail": "Pi-hole action endpoint returned an invalid response; the action may have partly run. Check the instance."}` |
| unreachable before send | 503 | `{"detail": "Unable to reach Pi-hole instance"}` |
| transport failure after send (audited `unconfirmed`, `reason: "transport_error"`) | 503 | `{"detail": "The action was sent but Pi-hole did not confirm it; it may have run. Check the instance."}` |

Audit lines: `action` ∈ `ftl_restart_dns | ftl_flush_logs | ftl_flush_network`,
`before: {}`, `after: {"status": "success"}` or
`{"status": "unconfirmed", "reason": "transport_error" | "http_<code>" | "invalid_response"}`.

Frontend (`frontend/src/api.ts`):

```ts
export type FtlAction = 'restart_dns' | 'flush_logs' | 'flush_network'

export interface FtlActionResult {
  instance: string
  action: FtlAction
}

// in api:
runFtlAction: (name: string, action: FtlAction) =>
  request<FtlActionResult>(
    `/api/instances/${encodeURIComponent(name)}/actions/${action}`,
    { method: 'POST' },
  ),
```

No config keys or DB changes.

## Tests

`tests/test_pihole_client.py` (use the existing `httpx.MockTransport` + preset `Session`
pattern from `test_adlist_requests_include_block_type_and_preserve_groups`):

- `test_run_action_posts_to_ftl_action_path` — parametrized over the three actions. The
  request is `POST` to the mapped path with session headers and an empty body. A
  `{"status":"success"}` reply returns `None`.
- `test_run_action_403_names_allow_destructive` — a 403 reply raises `PiholeAPIError`
  with `status_code == 403` and a message containing `webserver.api.allow_destructive`.
  The response body text does not appear in the message.
- `test_run_action_rejects_bad_responses` — parametrized over: a 500 reply (message
  `POST /api/action/flush/logs failed with HTTP 500`, `status_code == 500`), a 200 with
  `{"status":"error"}`, a 200 with a non-JSON body, and a 200 with a JSON list. Each
  raises `PiholeActionUnconfirmedError`. The 200 cases carry `status_code == 200`.
- `test_run_action_4xx_is_a_definite_refusal` — parametrized over 401, 403, and 404.
  Each raises `PiholeAPIError` that is *not* a `PiholeActionUnconfirmedError`.

`tests/test_api.py` (add `run_action` to `FakePiholeClient` that appends to a class-level
`actions` list, reset in `setup_api`):

- `test_instance_action_runs_and_audits` — parametrized over the three actions. The
  return value is `{"instance": "pihole1", "action": action}`, the fake recorded exactly
  `[action]`, and audits equal `[("pihole1", f"ftl_{action}", {}, {"status": "success"})]`.
- `test_instance_action_unknown_instance_404` — 404 `Instance not found`, no call, no audit.
- `test_instance_action_without_credential_409` — 409, no call, no audit.
- `test_instance_action_pihole_error_502` — `run_action` raises
  `PiholeAPIError("Pi-hole refused the action (HTTP 403); ...")`. The result is 502 with
  that detail and no audit.
- `test_instance_action_unreachable_before_send_503_not_audited` — the client's
  `__enter__` raises `httpx.ConnectError`. The result is 503 `Unable to reach Pi-hole
  instance` with no audit.
- `test_instance_action_transport_error_after_send_audits_unconfirmed` — `run_action`
  raises `httpx.ReadError`. The result is 503 with the "sent but … not confirmed" detail,
  and audits equal `[("pihole1", "ftl_restart_dns", {}, {"status": "unconfirmed", "reason": "transport_error"})]`.
- `test_instance_action_upstream_error_after_send_audits_unconfirmed` — parametrized over
  `run_action` raising `PiholeActionUnconfirmedError("POST /api/action/flush/logs failed with HTTP 500", status_code=500)`
  (reason `http_500`) and `PiholeActionUnconfirmedError("Pi-hole action endpoint returned an invalid response", status_code=200)`
  (reason `invalid_response`). The action is `flush_logs`. The result is 502, the detail
  is the message followed by `; the action may have partly run. Check the instance.`, and
  audits equal `[("pihole1", "ftl_flush_logs", {}, {"status": "unconfirmed", "reason": <reason>})]`.
- `test_instance_action_unconfirmed_survives_audit_failure` — `record` raises `OSError`
  and `run_action` raises the 500 `PiholeActionUnconfirmedError`. The route still raises
  the 502 with the "may have partly run" detail, not a 500.
- `test_instance_action_survives_audit_failure` — `record` raises `OSError`. The route
  still returns the success body.
- `test_instance_action_route_is_post_only_with_literal_action` — exactly one route at
  `/api/instances/{name}/actions/{action}`, methods `{"POST"}`.

## Invariants at risk

1. **One instance mutated at a time.** The route takes one instance name in the path and
   makes one action call. The frontend disables every row's Service menu while an action
   runs. There is no multi-instance variant.
2. **Diff before write.** These actions have no desired state, so there is nothing to
   diff. This plan records the decision that the confirm modal, with copy stating the
   consequence and irreversibility, is the pre-write gate. That matches the
   blocking-pause precedent, which also confirms and does not diff. No action is applied
   without an explicit user confirmation.
3. **Gravity gated behind real changes.** Untouched. None of these actions runs gravity,
   and the gravity-pending flag is neither read nor changed.
4. **No background scheduler.** All three actions are user-initiated, one click and one
   confirm each. No retries, timers, or follow-up jobs.
5. **Credentials stay in FastAPI.** The response carries only the instance name and the
   action. The 403 message is a fixed string, and Pi-hole's body is never echoed.
6. **Outbound requests via `url_policy`.** `run_action` uses the existing `PiholeClient`,
   whose transport is pinned to a destination already validated by
   `resolve_instance_destination`. No new URL source.
7. **Auth middleware.** The new route lives on the existing `/api` router behind the
   middleware. No exemption.
8. **Compiled frontend committed.** Step 5.
9. **`main` is production.** Work on `feature/0026-ftl-service-actions`.

## Acceptance criteria

- [ ] `POST /api/instances/<name>/actions/restart_dns|flush_logs|flush_network` each call
      exactly one `POST /api/action/...` on that instance, per the path map.
- [ ] An unknown action returns 422. An unknown instance returns 404. Neither reaches the
      Pi-hole client.
- [ ] A Pi-hole 403 surfaces as 502 with the `allow_destructive` message, visible as a
      toast in the UI.
- [ ] Successful actions write `ftl_<action>` audit lines with `{"status": "success"}`,
      visible and filterable on the Audit log page.
- [ ] A transport failure after send writes an `unconfirmed` audit line with
      `reason: "transport_error"` and returns 503.
- [ ] A Pi-hole 5xx or an unreadable 200 writes an `unconfirmed` audit line with
      `reason: "http_<code>"` or `"invalid_response"` and returns 502 with the
      "may have partly run" detail. A 4xx writes no audit line.
- [ ] Each Instances row shows a Service dropdown. Every item opens a confirm modal with
      the copy from Step 4. The flush items and their OK buttons are styled as danger.
      Cancel sends no request.
- [ ] While one action runs, every row's Service button is disabled and the active row's
      button shows loading.
- [ ] Live check against lab-pihole: Restart DNS succeeds, the instance answers again
      within a few seconds, and a Refresh shows it reachable. Flush network table
      succeeds. Run flush logs live only with the user's go-ahead, because it erases the
      lab instance's last 24 h of query history.
- [ ] Docs updated per Step 6.
- [ ] `./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
