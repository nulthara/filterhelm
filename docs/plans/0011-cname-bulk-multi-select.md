# 0011 — Bulk multi-select delete on the CNAME tab

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Revision 1: 2026-09-08 — Codex stopped at the plan-review gate on first handoff, before
editing anything, with one correct finding. Step 3's loop set `result["success"]` /
`result["changed"]` *after* the `record()` call, both inside the same per-record `try`, so
a failing audit write (a real failure mode here — `record()` does file I/O, and the 0010
verification hit a read-only `.git` filesystem) would land the delete on the instance but
report it back as `success: false, changed: false`. That is the exact
`docs/potential-features.md` §6 bug ("A failed audit write misreports a successful bulk
mutation (plan 0010)"), and this plan would have re-shipped it. Fixed here: the per-record
`try` now guards **only** `_cname_write_delete`; on success the result is settled from the
write's own outcome and appended, and `record()` runs afterward, outside that `try`. A
`record()` exception now propagates to the route's outer handler (a 5xx after a delete
that genuinely landed — the same shape the single-record `dns_cname_delete` already has),
never a per-record mislabel. One new test. Scope unchanged.
Branch: feature/0011-cname-bulk-multi-select
Author: Claude (architect)
Date: 2026-09-07

## Problem

Plan 0010 added row selection and bulk actions to Block Lists, Domains, and the Local
DNS A/AAAA table, and explicitly left the CNAME tab out. The CNAME tab
(`DNSCnamesTab`) is now the only list-shaped table in the app without selection — a
visible inconsistency, and the same one-record-at-a-time friction 0010 removed
everywhere else (deleting five stale CNAMEs across two instances is ten `Popconfirm`
clicks and ten authenticated Pi-hole sessions). This closes section 1 of
`docs/potential-features.md`.

The backlog note for this item is explicit that it is **not** a copy-paste of 0010:
plan 0008's live testing established that a `dns.cnameRecords` write briefly restarts
FTL, so the request immediately following one intermittently fails at the transport
level — `dns.hosts` writes do not do this, which is why 0010's plain per-record loop
needed no resilience machinery. A bulk CNAME delete is N sequential writes where each
lands inside the previous one's restart window, so it must go through 0008's
`_cname_write_delete` helper (transport-retry, 404-tolerant on retry) rather than a bare
`client.delete_dns_cname(...)` call.

## Scope

In:
- Row selection (AntD `rowSelection`) on the CNAME table in `DNSCnamesTab`, with the
  existing `BulkActionBar` (count, target-instance picker, Clear selection) shown only
  when rows are selected.
- Bulk **remove** only. CNAMEs have no enabled/disabled state, so there is no
  enable/disable to offer — this matches the A/AAAA table, which is also delete-only.
- One backend bulk endpoint, `POST /api/dns/cnames/bulk`, scoped to exactly one instance,
  applying a list of deletes sequentially inside a single Pi-hole session and returning a
  per-record result array — the same wire shape as 0010's three bulk endpoints.
- Each per-record delete goes through `_cname_write_delete` (0008), so the FTL-restart
  transport blip that follows each write is ridden out per record.
- A single confirm dialog per bulk action, itemising every alias and instance that will
  change before anything fires.
- Applying across several instances by issuing one per-instance request at a time,
  strictly sequentially, from the browser — same pattern as 0010.

Out:
- Bulk enable/disable/add/copy. "Copy here" stays per-record (there is no enable/disable
  for CNAMEs at all).
- Any change to the single-record CNAME endpoints (`POST`/`DELETE`/`PATCH /api/dns/cnames`)
  or their semantics, or to `_cname_settings_tree` / `_cname_write_add` /
  `_cname_write_delete` / `_parse_dns_cnames` / `_cname_entry` / `_cname_record`.
- Any change to the A/AAAA bulk path (`/api/dns/records/bulk`) or `DNSHostsTab`.
- Request chunking in the browser. 0010's DNS chunking exists because a `dns.hosts`
  hostname legitimately fans out to many addresses (9+ is normal), so a 25-row page
  selection can exceed the 200-record request cap. A CNAME alias is unique per instance
  — `POST /api/dns/cnames` 409s a second record for an existing alias — so a selected
  row expands to at most one record per instance, and a 25-row page cannot approach 200.
  The `Field(max_length=200)` cap stays as a defensive backend bound; the browser sends
  one request per instance with no chunk loop.
- Select-across-pages. Selection is cleared on page change, search change, and reload,
  exactly as 0010 does for the other three tables.
- Mid-batch abort. A genuine per-record failure (a real HTTP 4xx/5xx from Pi-hole, or
  `_cname_write_delete`'s transport-retry budget exhausting) is recorded against that one
  record and the batch continues — see Approach. There is no rollback or orphan path for
  a CNAME delete (unlike `dns.hosts`, a CNAME entry has no sibling to rewrite), so a
  failed delete simply leaves that record in place and cannot corrupt the instance.

## Approach

**A dedicated backend endpoint, not a browser loop over `DELETE /api/dns/cnames`.** The
same four reasons 0010's Approach gives apply unchanged: session churn (each single-record
call is a `POST /api/auth` + `DELETE /api/auth` and a full `GET /api/config` read), the
one-instance-at-a-time invariant is checkable in a route that resolves exactly one
`Instance` but only a convention in a browser loop, audit quality (one bulk intent, not N
interleaved lines), and uniform partial-failure reporting. The CNAME restart behaviour
adds a fifth: a browser loop of `await api.deleteDNSCname(...)` would hit the FTL-restart
window on nearly every call after the first and would need to reinvent `_cname_write_delete`
in TypeScript.

**The endpoint is per-instance by construction.** `instance` is a field on the request
body (matching the existing `POST`/`DELETE`/`PATCH /api/dns/cnames` and
`/api/dns/records/bulk`, none of which use a path parameter). The route resolves one
`Instance`, opens one `with _client(instance) as client:` session, reads the config tree
**once inside that session** (`client.get_config()` — **not** `_cname_settings_tree`,
which opens its own session and would make the request spend two, the exact defect
0010 Revision 2 caught for the DNS bulk route), and iterates a plain `for` loop. There is
no `_fanout` and no second instance reachable from the handler. Multi-instance
application is the browser's job, done by awaiting one instance's request fully before
starting the next.

**Diff before write, per record.** The route matches each requested `(hostname, target)`
pair structurally against the parsed current rows (same rule as `dns_cname_delete`:
normalised hostname **and** normalised target, not reconstructed-string equality, so a
stored `Alias.LAN,Server.LAN` still matches a lowercase ref). A pair that no longer
matches any row is reported `success: false, changed: false` with a reason and does not
fail the request. The confirm dialog itemises every alias and instance before the first
request goes out — the per-record `Popconfirm` is replaced by a per-batch confirm that
shows strictly more information.

**Each delete goes through `_cname_write_delete(instance, client, owner["raw"])`.** This
is the load-bearing difference from 0010. `_cname_write_delete` (0008, Revisions 3–5)
retries the delete across `httpx.TransportError` up to 4 attempts 1.5s apart, and on a
retry tolerates a `PiholeAPIError` with `status_code == 404` as proof the exact string is
already gone. That is exactly the shape a bulk delete needs: delete #1 triggers the
restart, delete #2's call catches the transport error and retries into the recovered
listener. The helper's raw string is `owner["raw"]` — the stored value, not the
reconstructed one — matching `dns_cname_delete`.

**A genuine per-record failure continues the batch (confirmed with the user).** After
`_cname_write_delete`'s transport retries, what can still propagate is a real
`PiholeAPIError` (a non-404 HTTP status, or a 404 on the very first attempt) or transport
exhaustion. The per-record handler records `{"success": false, "changed": false, "error": ...}`
for that alias and moves on, matching the adlist and domain bulk routes in 0010. This is
safe here in a way it would not be for `dns.hosts`: a CNAME entry has no sibling
hostnames, so `_delete_dns_host_record`'s `_rewrite_without` / rollback /
`_partial_dns_failure` machinery has no CNAME analogue — `dns_cname_delete` is a single
`DELETE` with no rollback branch. A failed CNAME delete leaves that one record present
and the instance otherwise untouched; there is no half-written state that would justify
aborting the rest of the batch. There is therefore **no `except HTTPException` /
abort-batch path** in this route, unlike `dns_records_bulk`.

**The result is settled from the write, then audited — never the other way around
(Revision 1).** `docs/potential-features.md` §6 documents that plan 0010's three bulk
loops set `result["changed"] = True` *after* `record()` inside the same `try`, so an
audit-write failure (disk full, a read-only filesystem — the 0010 verification actually
hit `.git/index.lock: Read-only file system`) reports a delete that already landed as a
failure. This route must not repeat that: the per-record `try` guards **only**
`_cname_write_delete`; once it returns, the result is marked `success/changed` and
appended, and `record()` is called afterward. If `record()` raises, it propagates to the
route's outer `except Exception` (a 5xx for a batch whose deletes genuinely landed — the
same failure shape the single-record `dns_cname_delete` already has), which is honest;
what it can never do is flip a landed delete's result to `success: false`.

**No gravity.** CNAMEs are local DNS, not adlists. The route calls neither
`mark_gravity_pending` nor `update_gravity`, matching `dns_cname_delete`. The response
has no `gravity_pending` field.

Rejected alternatives:

- **Reuse `dns_records_bulk` with a `kind` discriminator.** Rejected for the same reasons
  0008 rejected folding CNAMEs into `/api/dns/records`: different value grammar, different
  matching (target is a hostname, `_parse_dns_cnames` vs `_parse_dns_hosts`), and it would
  put the delicate `dns.hosts` rollback path on the same code path as the trivial CNAME
  one. A union response every caller narrows anyway.
- **Pi-hole's own `POST /api/config/dns/cnameRecords` array rewrite.** Rejected: violates
  the standing "item-level operation, never replace the whole array" rule
  (`app/routes/api.py` issues #6/#7) that every other `dns.*` write in this app follows.
- **Browser-side chunking like 0010.** Rejected as unnecessary — see Scope. A CNAME alias
  is unique per instance so a page-full selection cannot approach the 200 cap.
- **Aborting the batch on the first genuine failure.** Rejected (user-confirmed): diverges
  from the other three bulk routes and there is no data-corruption risk for a delete-only
  path with no rollback that would justify the divergence.

## Files

- `app/routes/api.py` — new `CNAMERecordRef`, `CNAMERecordBulkInput`,
  `CNAMERecordBulkResponse` models and the `dns_cnames_bulk` route
  (`POST /api/dns/cnames/bulk`). No change to any existing model, route, or helper.
- `frontend/src/api.ts` — new `CNAMERecordRef` interface and `bulkDNSCnames` method.
- `frontend/src/pages/DNSCnamesTab.tsx` — `rowSelection`, `BulkActionBar`, the
  `selectedKeys` / `bulkInstances` / `working` state, the `applyBulk` flow, and
  selection-clearing on page change, search change, and reload. Mirrors `DNSHostsTab`.
- `tests/test_api.py` — CNAME bulk tests (see Tests). `FakePiholeClient` already carries
  `cnameRecords` state, `deleted_cnames`, `delete_dns_cname`, and the `RetryingCnameClient`
  / `cname_retry_setup` fixture from 0008 — no new test scaffolding needed.
- `docs/design.md` — §5.2's multi-select bullet extended to name CNAME records.
- `docs/potential-features.md` — move the section-1 CNAME bulk item into the "Shipped
  since these notes were first written" list; leave the §1 heading with a
  "nothing outstanding" note so sections 2–6 keep their numbers.
- `app/static/ui/` — rebuilt frontend bundle (invariant #8).

`frontend/src/styles.css` is **not** touched — `.bulk-bar` and its mobile rule already
exist from 0010 and `BulkActionBar` is already imported by `DNSHostsTab`.

## Steps

1. **Branch** `feature/0011-cname-bulk-multi-select` from `main`.

2. **`app/routes/api.py` — models.** Beside `DNSRecordRef` / `DNSRecordBulkInput`
   (currently `api.py:163-171`):

   ```python
   class CNAMERecordRef(BaseModel):
       hostname: str = Field(min_length=1, max_length=253)
       target: str = Field(min_length=1, max_length=253)


   class CNAMERecordBulkInput(BaseModel):
       instance: str = Field(min_length=1, max_length=100)
       action: Literal["delete"]
       records: list[CNAMERecordRef] = Field(min_length=1, max_length=200)
   ```

   and beside `DNSRecordBulkResponse` (`api.py:147-148`):

   ```python
   class CNAMERecordBulkResponse(BulkResponse):
       action: Literal["delete"]
   ```

   `action` is a one-member `Literal` on purpose, exactly as `DNSRecordBulkInput`'s is: it
   keeps the wire shape identical to the other bulk endpoints and leaves room for a future
   action without inventing one now.

3. **`app/routes/api.py` — the route.** Add next to `dns_records_bulk` (after it, before
   `dns_record_delete` — or anywhere among the `/dns/cnames` routes; reviewer's choice, it
   has no ordering dependency):

   ```python
   @router.post("/dns/cnames/bulk", response_model=CNAMERecordBulkResponse)
   def dns_cnames_bulk(payload: CNAMERecordBulkInput) -> dict[str, Any]:
       instance = _instance(payload.instance)
       results: list[dict[str, Any]] = []
       requested: dict[tuple[str, str], CNAMERecordRef] = {}
       for ref in payload.records:
           key = (
               ref.hostname.strip().lower().rstrip("."),
               ref.target.strip().lower().rstrip("."),
           )
           requested.setdefault(key, ref)  # first-seen wins, matches next(...) in the single-record route
       try:
           with _client(instance) as client:
               rows = _parse_dns_cnames(client.get_config())
               for (hostname, target), ref in requested.items():
                   result: dict[str, Any] = {
                       "id": f"{hostname},{target}",
                       "success": False,
                       "changed": False,
                       "error": None,
                   }
                   owner = next(
                       (row for row in rows if row["hostname"] == hostname and row["target"] == target),
                       None,
                   )
                   if owner is None:
                       result["error"] = "CNAME no longer exists on this instance"
                       results.append(result)
                       continue
                   try:
                       _cname_write_delete(instance, client, owner["raw"])
                   except PiholeAPIError as exc:
                       result["error"] = str(exc)
                       results.append(result)
                       continue
                   except Exception:  # noqa: BLE001 - hide transport details
                       result["error"] = "Unable to update Pi-hole CNAME records"
                       results.append(result)
                       continue
                   # Revision 1: the delete landed — settle the result from the write's own
                   # outcome and append it BEFORE auditing. A failing record() must never
                   # relabel a delete that succeeded (docs/potential-features.md §6).
                   result["success"] = True
                   result["changed"] = True
                   results.append(result)
                   record(
                       payload.instance,
                       "dns_cname_delete",
                       {"dns.cnameRecords": owner["raw"]},
                       {"bulk": "delete"},
                   )
           return {"instance": payload.instance, "action": "delete", "results": results}
       except HTTPException:
           raise
       except Exception as exc:  # noqa: BLE001
           raise _api_error(exc) from exc
   ```

   Notes for the reviewer:
   - The config read is `client.get_config()` **inside** the session, not
     `_cname_settings_tree(instance)` — one session per request regardless of batch size
     (0010 Revision 2's defect; there is a test for it below).
   - `requested` dedupes on the normalised `(hostname, target)` pair and reports each
     result under the first ref submitted for that pair — so `Alias.LAN` / `alias.lan.` /
     `alias.lan` for one stored record produce one delete and one result, not three.
     `target` is part of the key because the same alias could in principle be submitted
     with two targets in one batch; only one can match a stored row (alias uniqueness),
     the other is reported "no longer exists".
   - No `_cname_record(...)` validation of the ref (which would 422 the whole batch on a
     malformed name). A malformed hostname or target simply matches no row and is reported
     per-record as "no longer exists" — the correct bulk-shaped outcome, same choice 0010
     made for `DNSRecordRef`.
   - No `except HTTPException` inside the per-record `try`: `_cname_write_delete` never
     raises `HTTPException`, and there is no `_partial_dns_failure` path for a CNAME
     delete. Transport-retry exhaustion surfaces as `httpx.TransportError` → the bare
     `except Exception` → generic message; the batch continues.
   - **(Revision 1)** The per-record `try` wraps **only** `_cname_write_delete`. The
     result is set to `success/changed` and appended after it returns; `record()` is
     called *after* that, outside the `try`. A `record()` exception therefore is **not**
     caught per-record — it propagates to the outer `except Exception` and 5xxs the
     request (a delete that landed, an audit line that didn't — the same edge the
     single-record route has). It must never turn a landed delete into a
     `success: false` result. `docs/potential-features.md` §6 is the anti-pattern.
   - No gravity call. No `gravity_pending` in the response.

4. **`frontend/src/api.ts` — type and method.** Beside `DNSRecordRef` (`api.ts:16`):

   ```ts
   export interface CNAMERecordRef {
     hostname: string
     target: string
   }
   ```

   and beside `bulkDNSRecords` (`api.ts:282`):

   ```ts
   bulkDNSCnames: (instance: string, records: CNAMERecordRef[]) =>
     request<{ instance: string; action: 'delete'; results: BulkResult[] }>('/api/dns/cnames/bulk', {
       method: 'POST',
       body: JSON.stringify({ instance, action: 'delete', records }),
     }),
   ```

5. **`frontend/src/pages/DNSCnamesTab.tsx` — selection and apply.** Mirror `DNSHostsTab`:

   - Add imports: `BulkActionBar` from `./BulkActionBar`, and `type BulkAction`,
     `type BulkResult` from `../api`.
   - Add state: `selectedKeys: string[]` (default `[]`), `bulkInstances: string[]`
     (default `[]`), `working: boolean` (default `false`).
   - In `load()`: `setSelectedKeys([])` at the top (as `DNSHostsTab.load` does), and
     `setBulkInstances(result.instance_names)` alongside `setInstanceNames(...)`.
   - In the mount `useEffect`: `setBulkInstances(result.instance_names)` alongside
     `setInstanceNames(...)`.
   - In the search `Input` `onChange`: add `setSelectedKeys([])` next to the existing
     `setSearch(...)` / `setCurrentPage(1)`.
   - On the `Table`: add
     `rowSelection={{ selectedRowKeys: selectedKeys, onChange: (keys) => setSelectedKeys(keys.map(String)), getCheckboxProps: () => ({ disabled: working }) }}`.
   - Change the `Table` `pagination.onChange` from `setCurrentPage` to
     `(page) => { setCurrentPage(page); setSelectedKeys([]) }`.
   - Render, immediately before the `<Table>`:

     ```tsx
     <BulkActionBar
       selectedCount={selectedKeys.length}
       instanceNames={instanceNames}
       selectedInstances={bulkInstances}
       onInstancesChange={setBulkInstances}
       actions={[{ key: 'delete', label: 'Remove', danger: true }]}
       onAction={(action) => void applyBulk(action)}
       onClear={() => setSelectedKeys([])}
       disabled={working}
     />
     ```

   - Add `applyBulk`, closely following `DNSHostsTab.applyBulk` but without the chunk loop:

     ```tsx
     const applyBulk = async (_action: BulkAction) => {
       const targets = bulkInstances.map((instance) => ({
         instance,
         records: rows
           .filter((row) => selectedKeys.includes(row.hostname))
           .flatMap((row) => (row.instances[instance] ?? []).map((r) => ({ hostname: r.hostname, target: r.target }))),
       })).filter((target) => target.records.length > 0)
       if (!targets.length) {
         message.warning('No selected records exist on the target instances')
         return
       }
       const confirmed = await modal.confirm({
         title: 'Remove selected CNAME records?',
         okText: 'Apply changes',
         okButtonProps: { danger: true },
         content: (
           <div>
             <Typography.Paragraph type="warning">Every alias listed below will be removed.</Typography.Paragraph>
             <Typography.Paragraph>Changes apply sequentially, one instance at a time.</Typography.Paragraph>
             {targets.map((target) => (
               <div key={target.instance}>
                 <Typography.Text strong>{target.instance}: {target.records.length} records</Typography.Text>
                 <List size="small" dataSource={target.records} renderItem={(item) => <List.Item>{`${item.hostname} → ${item.target}`}</List.Item>} />
               </div>
             ))}
           </div>
         ),
       })
       if (!confirmed) return
       setWorking(true)
       try {
         const results: Array<BulkResult & { instance: string }> = []
         const instanceFailures: string[] = []
         // Invariant #1: await one instance at a time; concurrent logins can race into a 401.
         for (const target of targets) {
           try {
             const response = await api.bulkDNSCnames(target.instance, target.records)
             results.push(...response.results.map((r) => ({ ...r, instance: target.instance })))
           } catch (error) {
             instanceFailures.push(`${target.instance}: ${error instanceof Error ? error.message : 'Unable to apply bulk action'}`)
           }
         }
         const changed = results.filter((r) => r.changed).length
         const unchanged = results.filter((r) => r.success && !r.changed).length
         const failures = [
           ...results.filter((r) => !r.success).map((r) => `${r.instance} / ${r.id}: ${r.error}`),
           ...instanceFailures,
         ]
         const summary = `${selectedKeys.length} selected, ${changed} changed${unchanged ? `, ${unchanged} already removed` : ''}`
         if (failures.length) message.warning(`${summary}; failed: ${failures.join('; ')}`, 10)
         else message.success(summary)
         setSelectedKeys([])
         await load()
       } finally {
         setWorking(false)
       }
     }
     ```

     `List` needs adding to the `antd` import in this file (it is not currently imported;
     `DNSHostsTab` imports it). `Typography` is already imported.

6. **Rebuild the bundle.** `cd frontend && npm run build && npm run lint`, commit
   `app/static/ui/`.

7. **Docs.**
   - `docs/design.md` §5.2: change the first bullet from
     "Multi-select A/AAAA records to remove them with a single confirm, applied
     sequentially one instance at a time." to
     "Multi-select A/AAAA and CNAME records to remove them with a single confirm, applied
     sequentially one instance at a time." (§8's closing note already covers bulk
     generically and needs no change.)
   - `docs/potential-features.md`: remove the CNAME bulk bullet from section 1; add to the
     "Shipped since these notes were first written" list near the top:
     "- Bulk / multi-select delete for CNAME records on the Local DNS screen, each delete
     through 0008's `_cname_write_delete` transport-retry helper, with one confirm and
     sequential per-instance requests — plan 0011 (design.md §5.2)."
     Leave the "## 1. Extensions of existing patterns" heading and its intro paragraph in
     place with a line noting nothing is outstanding, so sections 2–6 keep their numbers.

## Contracts

### `POST /api/dns/cnames/bulk` (new)

Request (`CNAMERecordBulkInput`):

```json
{
  "instance": "lab-pihole",
  "action": "delete",
  "records": [{ "hostname": "alias.lan", "target": "server.lan" }]
}
```

Response (`CNAMERecordBulkResponse`):

```json
{
  "instance": "lab-pihole",
  "action": "delete",
  "results": [
    { "id": "alias.lan,server.lan", "success": true, "changed": true, "error": null }
  ]
}
```

- `id` is `"<normalised hostname>,<normalised target>"`.
- `success: true, changed: false` is not reachable for this route (a delete either removes
  the record or reports it missing); the field is present for wire-shape parity with the
  other bulk endpoints. A record that no longer matches any row comes back
  `success: false, changed: false, error: "CNAME no longer exists on this instance"` and
  does **not** fail the request.
- One genuine per-record failure (real Pi-hole HTTP error, or `_cname_write_delete`
  transport-retry exhaustion) is reported in that record's `error` and the batch
  continues. There is no batch-abort / 502 path for a Pi-hole write failure (contrast
  `/api/dns/records/bulk`).
- No `gravity_pending` field — CNAMEs never mark gravity.
- Errors for the whole request: 404 unknown instance; 409 no stored credential (from
  `_client`); 422 more than 200 records or `action != "delete"`; 502/503 from `_api_error`
  if the initial config read fails, **or if an audit `record()` write fails after a delete
  has landed (Revision 1)** — in that last case the deletes that ran genuinely happened;
  the per-record results are not returned. A record that was deleted is never reported
  `success: false`.

### `api.ts`

`CNAMERecordRef { hostname: string; target: string }` and
`bulkDNSCnames(instance, records) → Promise<{ instance: string; action: 'delete'; results: BulkResult[] }>`
as written in step 4. No existing method signature changes.

### Config keys / DB

N/A — no new config key, no new stored state. `dns.cnameRecords` is read out of the
existing `GET /api/config` tree via `get_setting`; nothing is added to `MANAGED_FIELDS`.

## Tests

All in `tests/test_api.py`. `FakePiholeClient` already has
`tree["dns"]["cnameRecords"]`, `deleted_cnames`, `delete_dns_cname`, and the
`cname_retry_setup` fixture + `RetryingCnameClient` subclass exist from 0008.

- `tests/test_api.py::test_cname_bulk_delete_removes_each_record` — tree holds
  `["a.lan,x.lan", "b.lan,y.lan"]`; a batch deleting both asserts
  `deleted_cnames == ["a.lan,x.lan", "b.lan,y.lan"]`, both results
  `success: true, changed: true`, and `action == "delete"` in the response.
- `tests/test_api.py::test_cname_bulk_delete_uses_stored_raw_string` — tree holds
  `["Alias.LAN,Server.LAN"]`; the ref is `{"hostname": "alias.lan", "target": "server.lan"}`;
  asserts `deleted_cnames == ["Alias.LAN,Server.LAN"]` (the stored string, not the
  reconstructed one).
- `tests/test_api.py::test_cname_bulk_delete_reports_missing_record` — a ref for an alias
  not on the instance; asserts the result is `success: false, changed: false` with
  "CNAME no longer exists on this instance", the endpoint returns 200 (does not raise),
  and `deleted_cnames == []`.
- `tests/test_api.py::test_cname_bulk_delete_dedupes_repeated_ref` — one stored record
  `["alias.lan,server.lan"]`; a batch submitting `("Alias.LAN", "server.lan")`,
  `("alias.lan.", "server.lan.")`, and `("alias.lan", "server.lan")` together; asserts
  `delete_dns_cname` was called exactly once and the response carries exactly one result.
- `tests/test_api.py::test_cname_bulk_delete_continues_after_one_failure` — a
  `FakePiholeClient` subclass whose `delete_dns_cname` raises
  `PiholeAPIError("boom", status_code=500)` for the second of three records; asserts all
  three results are present, the first and third were deleted (`deleted_cnames` has both),
  and the second result carries a non-null `error`.
- `tests/test_api.py::test_cname_bulk_delete_rides_out_transport_blip` — a client whose
  `delete_dns_cname` raises `httpx.RemoteProtocolError` once then succeeds (the
  `RetryingCnameClient` shape); asserts the record is still deleted and its result is
  `success: true` with no error — proving the route calls `_cname_write_delete`, not a bare
  `client.delete_dns_cname`.
- `tests/test_api.py::test_cname_bulk_delete_is_sequential_single_instance` — a client that
  counts constructions; asserts exactly one `PiholeClient` was constructed for a
  multi-record batch (guards against reintroducing `_cname_settings_tree` inside the
  route — 0010 Revision 2's two-session defect).
- `tests/test_api.py::test_cname_bulk_delete_audits_each_removed_record` — monkeypatch
  `api.record`; a batch of two present records and one missing; asserts exactly two
  `dns_cname_delete` audit calls, each with `{"dns.cnameRecords": <raw>}` before and
  `{"bulk": "delete"}` after, and none for the missing record.
- **(Revision 1)** `tests/test_api.py::test_cname_bulk_delete_does_not_mislabel_when_audit_fails`
  — monkeypatch `api.record` to raise `OSError` on its second call; a batch of two present
  records. Asserts both writes landed (`deleted_cnames` has both raw strings) and the route
  raises rather than returning a result where the first record — whose delete *and* audit
  both succeeded — or the second — whose delete succeeded — is reported `success: false`.
  This is the guard against re-shipping the `docs/potential-features.md` §6 bug: the
  pre-Revision-1 ordering would have caught the `OSError` per-record and set
  `success: false, changed: false` for a delete that had already landed.
- `tests/test_api.py::test_cname_bulk_rejects_oversized_batch` — 201 records raises
  `pydantic.ValidationError`.
- `tests/test_api.py::test_cname_bulk_rejects_unknown_action` — `action="disable"` raises
  `pydantic.ValidationError`.

No frontend tests: `frontend/` has no test runner (documented in
`docs/potential-features.md` §5). `DNSCnamesTab`'s `applyBulk` is a near-copy of
`DNSHostsTab`'s already-shipped handler minus the chunk loop; live verification against
lab-pihole (below) is the gate, matching plan 0010.

## Invariants at risk

1. **One instance mutated at a time.** The closest invariant. `dns_cnames_bulk` takes a
   single `instance` field, resolves exactly one `Instance` via `_instance(...)`,
   constructs one `PiholeClient`, and iterates a plain `for` loop — no `_fanout`, no
   `ThreadPoolExecutor`, no second instance reachable from the handler. Multi-instance
   application happens in the browser as an awaited sequential loop with an explanatory
   comment; `Promise.all` is not used. **The reviewer should grep the new route and the
   `DNSCnamesTab` diff for `_fanout` and `Promise.all`.** A test asserts exactly one
   `PiholeClient` per request.
2. **Diff before write.** The route reads the current `dns.cnameRecords` array once at the
   top of the session and matches every requested pair structurally against it. A pair
   that vanished is reported, not blindly deleted. The confirm dialog itemises every alias
   and instance before the first request — the per-record `Popconfirm` is replaced by a
   per-batch confirm showing strictly more.
3. **Gravity gated behind real changes.** Not touched — CNAMEs are local DNS. The route
   calls neither `mark_gravity_pending` nor `update_gravity`, matching `dns_cname_delete`.
   No `gravity_pending` in the response.
4. **No background scheduler.** Request-scoped, initiated by the "Remove" button in the
   bulk bar. Nothing runs on a timer. `_cname_write_delete`'s `time.sleep(1.5)` retries
   are bounded (≤3 per record) and run inside the request, same as the single-record
   CNAME routes already do.
5. **FastAPI owns credentials.** The route goes through `_client(instance)` /
   `CredentialStore`. The new `api.ts` `CNAMERecordRef` type and the `DNSCnamesTab`
   changes carry instance names, hostnames, and targets only — no Pi-hole URL, password,
   or session id.
6. **Outbound requests via `url_policy`.** No new outbound destination. `delete_dns_cname`
   is a path on the already-validated `PiholeClient` base URL, whose destination went
   through `resolve_instance_destination` at construction. No user-supplied URL is
   fetched, so `screen_adlist_url` is not involved.
7. **Every route behind auth middleware.** `dns_cnames_bulk` is registered on the existing
   `router = APIRouter(prefix="/api")` in `app/routes/api.py`, already mounted behind the
   auth middleware. No route-level exemption.
8. **Compiled frontend committed.** Step 6 rebuilds and commits `app/static/ui/`.
9. **`main` is production.** Work lands on `feature/0011-cname-bulk-multi-select`, merged
   only after an Accept verdict.

Additional hardening note: the batch is capped at 200 records via
`Field(max_length=200)`, and the existing request-body size limit in `http_hardening.py`
applies unchanged. Worst-case request duration is bounded by 200 × `_cname_write_delete`'s
retry budget (4 attempts × 1.5s), but realistic batches are 10–20 records and the restart
window only affects the request immediately after a write, so a handful of retries across
a batch is the practical ceiling. `DELETE` semantics and same-origin / Fetch-Metadata
checks are unchanged — this is a `POST` with a JSON body, the established pattern.

## Acceptance criteria

- [x] Selecting rows on the CNAME tab reveals the bulk bar with the selection count, the
      target-instance picker, and a single "Remove" action; clearing the selection hides
      it.
- [x] "Remove" opens exactly one confirm dialog listing every affected alias and instance
      before anything is written.
- [ ] A bulk remove across two instances issues one request per instance, strictly one
      after the other — verifiable in the browser network panel as non-overlapping
      requests.
- [ ] Each bulk request constructs exactly one Pi-hole session regardless of batch size
      (asserted by test, and visible as one `POST /api/auth` per instance).
- [ ] A CNAME that disappeared between page load and apply is reported per-record
      ("CNAME no longer exists on this instance") without failing the whole batch.
- [ ] One record's genuine failure does not abandon the rest of the batch; there is no
      502 batch-abort path for a Pi-hole write failure.
- [ ] **(Revision 1)** A record that was successfully deleted is never reported
      `success: false` — the result is settled from the write, then audited, so a failing
      `record()` cannot relabel a landed delete (guards `docs/potential-features.md` §6).
- [x] A bulk delete rides out the FTL-restart transport blip that follows each
      `dns.cnameRecords` write — the batch completes without a spurious "unavailable"
      error, because each delete goes through `_cname_write_delete`.
- [x] Changing the table page or the search box clears the row selection, so a bulk
      action can never touch a record the user cannot currently see.
- [x] The confirm summary reports counts honestly ("N selected, M changed").
- [x] Every existing single-record CNAME action (add, edit, delete, Copy here) and the
      A/AAAA tab behave exactly as before.
- [x] **Live verification against `lab-pihole`** (the gate plans 0007/0008/0010 set) —
      **done, 2026-09-08.** The user added CNAME records on lab-pihole, selected them on
      the tab, and bulk-removed them through the real UI. Confirmed: one confirm dialog,
      the records gone on refresh, no false "unavailable" error on the post-apply refresh.
      User: "I completed the live verification and it's all good."
- [x] `./.venv/bin/pytest` green (includes the Revision 1 audit-ordering test) — 337 passed.
- [x] `npm run build` + `npm run lint` clean, `app/static/ui/` committed — rebuilt after
      the reviewer's `void _action` cleanup; `index.html` references the single rebuilt
      `assets/index-*.js` on disk; lint shows only the pre-existing `App.tsx:33` warning.
- [x] `docs/design.md` §5.2 and `docs/potential-features.md` updated.

## Implementation verification — 2026-09-08

- Implemented all seven steps and all 11 specified backend tests, including the
  Revision 1 audit-write failure regression. Existing routes and helpers are unchanged.
- `./.venv/bin/pytest`: **337 passed in 2.25s**.
- `cd frontend && npm run build && npm run lint`: passed. Existing warnings remain:
  Vite's empty React chunk and bundle-size warning, and `App.tsx:33`'s hook dependency
  warning (lint: 0 errors, 1 warning). `app/static/ui/` was regenerated.
- `git diff --check`: passed. The new backend route and frontend handler use sequential
  loops without `_fanout` or `Promise.all`.
- Minimal lint adjustment to step 5's snippet: `void _action` marks the intentionally
  unused parameter as consumed; no behavior change. This plan file is also included as
  the change record required by ARCHITECT.md and to record verification status.
- Live verification against `lab-pihole` was pending at implementation time (the Codex
  environment had no browser). The user has since run it — see the acceptance criterion
  above.
- Commit was blocked in the Codex environment: `git add` failed with
  `Unable to create .git/index.lock: Read-only file system`.

## Review — 2026-09-08 (Accept)

Reviewed the working-tree diff against the plan and the ARCHITECT.md invariants.

- **Scope** — every changed file is on the plan's Files list; no existing model, route, or
  helper touched. `git diff main --stat`: `api.py` +73, `api.ts` +10, `DNSCnamesTab.tsx`
  +~80, `test_api.py` +171, two doc lines, the rebuilt bundle.
- **Invariants** — the new route resolves one `Instance`, opens one `_client` session,
  reads `client.get_config()` once inside it, iterates a plain `for`; no `_fanout`, no
  `Promise.all` (browser loop `await`s one instance at a time with the invariant-#1
  comment). No gravity call. No new outbound URL. Registered on the existing
  `APIRouter(prefix="/api")`.
- **Revision 1 fix present** — per-record `try` wraps only `_cname_write_delete`; result
  is settled and appended, then `record()` runs outside the `try`.
  `test_cname_bulk_delete_does_not_mislabel_when_audit_fails` asserts both deletes land,
  both audits are attempted, and the route raises 503 rather than reporting a landed
  delete as `success: false`.
- **Tests** — 337 passed (11 new, real assertions). Single-record CNAME/DNS tests
  unchanged.
- **Build hygiene** — `npm run build` + `npm run lint` clean; `app/static/ui/index.html`
  references exactly the rebuilt `assets/index-*.js` present on disk.
- **Reviewer fixup** — replaced Codex's `void _action` lint workaround by dropping the
  unused `applyBulk` parameter and the now-unused `BulkAction` import; rebuilt the bundle.
  No behaviour change.

Staged and committed on `feature/0011-cname-bulk-multi-select`, excluding nothing (no
unrelated working-tree changes this time).
