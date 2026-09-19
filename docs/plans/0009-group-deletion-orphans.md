# 0009 — Group deletion: orphaned adlist/domain resolution

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Revision 1: 2026-09-06 — Re-reviewed after plan 0008 shipped (five revisions there, all
caught by Codex's plan-review gate, turned up a real Pi-hole behavior: a `dns.cnameRecords`
write briefly restarts something in FTL that `dns.hosts` writes do not, which the original
0008 plan had assumed away by analogy). Checked this plan against the same question — does
the orphan cascade's sequential adlist/domain writes risk the same kind of restart-window
failure? No code change results, but it's worth recording why: adlists, domains, and groups
all go through their own dedicated REST resources (`/api/lists`, `/api/domains`,
`/api/groups`), not the generic `/api/config/dns/...` item-write path that `dns.hosts` and
`dns.cnameRecords` share, and sequential writes to all three have already been exercised live
without incident (plan 0007's group-targeting verification; plan 0006's group/client
management). This plan's per-entry `except PiholeAPIError` / `except Exception` handling
(Step 5.4) already does the conservative thing on an unexpected transient failure — record it
and continue, leaving the group in place — rather than guessing at success, so no
CNAME-style retry/verification machinery is warranted here. Separately, checked what happens
if the group being deleted is itself named "Default" (id 0) with `orphans: "reassign"`
selected: that would reassign the orphans to the very group about to be deleted, which is
nonsensical. At the time of this revision, believed Pi-hole rejects deleting the Default
group outright — **corrected in Revision 2 below; that belief was wrong.**
Revision 2: 2026-09-06 — The user corrected Revision 1's assumption: Pi-hole's built-in
Default group **can** be deleted through the API, and doing so can leave the instance in an
inconsistent state severe enough that reinstalling Pi-hole is the commonly cited fix (manually
recreating the Default group and reassigning every entry to it may also work, but isn't
guaranteed). This is a materially worse outcome than anything else this plan handles — an
orphaned adlist is a nuisance fixed by this same delete flow; a corrupted Default group can
cost the user their whole Pi-hole setup. Per the user's explicit direction, deleting the
Default group now requires its own deliberate, intentional acceptance gate, distinct from and
in addition to the ordinary orphan-choice confirm every other group delete already gets:
- **Backend**: `GroupDeleteInput` gains `confirm_default: bool = False` (Step 4). `group_delete`
  checks `before.id == 0` (the id, not the display name — Pi-hole's Default group is
  structurally id 0; a renamed group would still be it) immediately after resolving the group,
  *before* any orphan handling begins, regardless of the chosen `orphans` action — the danger
  is deleting group 0 itself, independent of what happens to its orphans. Missing confirmation
  → `HTTPException(400, ...)` naming the real risk, no Pi-hole call made (Step 5).
- **Frontend**: the delete modal (Step 7) detects `id === 0` on the target instance and renders
  a distinct, more severe `Alert type="error"` (not the ordinary orphan `warning`) stating the
  risk in the terms above, plus a required `Checkbox` — "I understand this may corrupt this
  Pi-hole instance and could require reinstalling it" — that the modal's OK button stays
  disabled without. This gate is independent of and layered on top of the existing
  orphan-choice radio group; both must be satisfied to enable OK when the target is Default.
- This is a genuine, if narrow, scope extension beyond Revision 1's "no additional guard
  needed" — justified because the severity discovered (data loss requiring a possible full
  reinstall) is an order of magnitude past what the rest of this plan's orphan-handling logic
  protects against, and a plain per-record error message (Revision 1's original mitigation) is
  not an adequate warning for a mistake this expensive to undo. Scope otherwise unchanged; see
  the updated Steps, Contracts, Tests, and Acceptance criteria below for the concrete design.
Revision 3: 2026-09-07 — Codex's plan-review gate caught a hole Revision 2 left open. The
plan permits `orphans: "reassign"` together with deleting the Default group itself (`id 0`,
`confirm_default: true`). An orphan of group 0 has `groups == (0,)`; "reassigning" it to
`[0]` is a no-op that Step 5 would record as success, then delete group 0 — re-stranding the
very entries the user asked to rescue, contradicting the "Move them to the Default group"
outcome they picked. Revision 1 named this combination "nonsensical" but Revision 2 pivoted
to the acknowledgment checkbox without closing it. Fix: `reassign` is rejected whenever the
target is group 0, at both layers.
- **Backend**: `group_delete`, immediately after the Revision 2 `confirm_default` check and
  before any orphan handling, if `before.id == 0 and payload.orphans == "reassign"` →
  `HTTPException(400, "Cannot reassign entries to the Default group while deleting it; "
  "choose 'delete' or 'ignore' instead.")`. No Pi-hole mutation has happened yet. This is a
  data-dependent check (does the named group happen to be id 0), not request-shape
  validation, so it lives in the route body, not a Pydantic validator — same rationale as
  the `confirm_default` check.
- **Frontend**: when the target group's id is 0 on that instance, the delete modal's
  `Radio.Group` offers only `ignore` and `delete` — the `reassign` option is not rendered —
  and the default selection becomes `ignore` (for the Default group there is no
  non-destructive reassign target; `ignore` is the least-destructive remaining choice, and
  the user is already being warned not to proceed at all). For every non-Default group the
  three options and the `reassign` default are unchanged.
- Scope otherwise unchanged. `reassign` continues to work exactly as before for all other
  groups. See the updated Steps 5 and 7, Contracts, Tests, and Acceptance criteria.
Branch: feature/0009-group-deletion-orphans
Author: Claude (architect)
Date: 2026-09-06

## Problem

Deleting a group from the Groups page removes the group, but Pi-hole silently drops the
`*_by_group` association rows for every adlist and domain that was scoped to it. An entry
whose *only* group was the deleted one is left with an empty group set: it stays in the
Block Lists / Domains tables looking normal, but it is no longer attached to anything, so
it either applies to nothing or applies unpredictably depending on how FTL treats an
unassociated entry. Found while verifying plan 0007 and recorded in
`docs/potential-features.md` §1. Today the user gets no warning at delete time and no way
to find the stranded entries afterwards short of eyeballing every row's group tags.

## Scope

In:
- A read-only preview endpoint that, for one instance and one group, reports the adlists
  and domains whose group set is *exactly* that one group.
- Three explicit outcomes chosen at delete time: **delete group only** (today's
  behaviour), **delete the group and the orphaned entries**, or **reassign the orphaned
  entries to the Default group (id 0)** and then delete the group.
- A Groups-page delete modal that fetches the preview, lists what would be stranded, and
  makes the user pick one of the three before the delete fires.
- Per-entry result reporting so a partial failure names exactly which entries were and
  were not handled, and leaves the group itself intact when orphan handling failed.
- Marking gravity pending for the instance when the chosen action changed any adlist.

Out:
- Any behaviour change for entries in *more than one* group. Deleting a group they belong
  to leaves them attached to their other groups, which is correct; they are not orphans
  and are not listed.
- Multi-instance group deletion. Deletion stays one instance per operation, as today.
- Bulk group deletion — out of plan 0010's scope too, by the answered scope question.
- Any periodic or dashboard-side "orphaned entries" sweep (option (c) in the backlog note).
  A user who wants to find pre-existing orphans still has to look at the tables.
- Retroactive cleanup of entries already orphaned by past deletions.
- Changing what Pi-hole itself does on `DELETE /api/groups/{name}`.

## Approach

The orphan set is computed, not stored: an entry is an orphan of group *G* on instance *I*
iff its `groups` tuple equals `(G.id,)` on that instance. Both `get_adlists()` and
`get_domains()` already return `groups`, so no new Pi-hole client capability is needed —
this is a filter over two reads plus a group-name→id lookup.

The preview is a **separate read-only GET**, and the delete **recomputes the orphan set
server-side inside the mutating session** rather than trusting whatever the browser was
shown. That is the diff-before-write rule applied literally: the preview is advisory UI,
and the authoritative comparison happens against state read moments before the write. If
the recomputed set differs from what the user approved, the delete still proceeds on the
recomputed set — the alternative (409 on any drift) would be a nuisance on a single-user
self-hosted tool, and the per-entry results returned to the browser say exactly what was
touched. The Reviewer should check that the response makes that visible.

Ordering matters and is fixed: **orphans are handled first, the group is deleted last.**
If an orphan delete or reassign fails, the group survives and the whole operation is
retryable from a coherent state. The reverse order would strand the entries with no group
to find them by.

The three-way choice rides on the existing `DELETE /api/groups/{instance_name}` body as a
new `orphans` field defaulting to `"ignore"`, rather than becoming three endpoints. The
default preserves today's behaviour for any caller that does not send the field, so the
contract change is additive.

Rejected alternatives:

- **Warn-and-link only** (option (b) in the backlog). Rejected by the answered scope
  question: it tells the user there is a mess without offering to clean it, and finding the
  entries afterwards means scanning group tags by eye.
- **Always cascade-delete.** Rejected: destructive by default for entries the user may
  simply want to re-scope. Reassign-to-Default is the non-destructive answer and has to be
  reachable.
- **Compute orphans in the browser from the already-loaded Adlists/Domains tables.**
  Rejected: the Groups page doesn't load those tables, group ids are instance-local, and it
  would put the diff on the untrusted side of the boundary. The recompute must happen in
  the same backend session that does the write.
- **A single "cleanup orphans" endpoint decoupled from deletion.** Rejected for this plan:
  it is a fine idea for the drift dashboard later, but it doesn't solve the "I am deleting a
  group right now and don't know what it will strand" problem.

Note on the Default group: reassign targets id `0`, which is Pi-hole's built-in Default
group and is the same value `add_adlist` / `add_domain` already use for new entries. The
Groups page's existing tooltip already warns that disabling Default disables everything
still assigned to it, so the semantics are consistent with what the UI already teaches.

## Files

- `app/routes/groups.py` — orphan computation helper, the preview route, the extended
  delete input model, and the rewritten `group_delete`. Revision 2 adds the `confirm_default`
  field and the id-0 pre-check. Revision 3 adds the id-0 + `reassign` rejection.
- `frontend/src/api.ts` — `GroupOrphans` / `GroupOrphanAdlist` / `GroupOrphanDomain` types,
  the `OrphanAction` union, `groupOrphans()`, and a new `orphans` argument on `deleteGroup`.
  Revision 2 adds a `confirmDefault` argument.
- `frontend/src/pages/GroupsPage.tsx` — replace the delete `Popconfirm` with a delete modal
  that previews orphans and carries the three-way choice. Revision 2 adds the Default-group
  warning and its acknowledgment checkbox. Revision 3 hides the `reassign` option and
  defaults to `ignore` when the target is the Default group.
- `tests/test_groups.py` — orphan computation, the three delete paths, ordering,
  partial-failure behaviour, gravity marking, (Revision 2) the Default-group
  confirmation gate, and (Revision 3) the id-0 + `reassign` rejection. Extend the fake
  client with domain and adlist state.
- `docs/design.md` — §5.6 gains the deletion semantics.
- `docs/potential-features.md` — move the group-deletion item from §1 to the shipped list.
- `app/static/ui/` — rebuilt frontend bundle (invariant #8).

## Steps

1. **`app/routes/groups.py` — imports.** Add `Literal` to the typing import, and
   `from app.config import ... mark_gravity_pending` alongside the existing config imports.
   Import `Adlist` and `Domain` from `app.pihole_client` for type hints.

2. **Orphan computation helper.** Add:

   ```python
   def _orphans(client: PiholeClient, group_id: int) -> tuple[list[Adlist], list[Domain]]:
       """Adlists and domains whose only group is `group_id` — the entries a
       delete of that group would strand with no group association at all.
       Entries in additional groups survive the delete correctly and are not
       returned.
       """
       adlists = [item for item in client.get_adlists() if tuple(item.groups) == (group_id,)]
       domains = [item for item in client.get_domains() if tuple(item.groups) == (group_id,)]
       return adlists, domains
   ```

   Add `_orphan_adlist_data(item)` → `{"address", "comment", "enabled"}` and
   `_orphan_domain_data(item)` → `{"domain", "type", "kind", "comment", "enabled"}` for the
   wire shape. Note `_group_data` currently omits `name`; keep it as is.

3. **`GET /api/groups/{instance_name}/orphans`.** Query parameter `name: str` (the group
   name; `Query(min_length=1, max_length=100)`). Resolve the instance with `_instance`, open
   one client, `_find_group(client, name)` (404 if the group is not on that instance), 409
   with "This group has no id on this instance" if `group.id is None` — an id is required to
   match `groups` tuples and its absence means the instance's response was malformed rather
   than the group being empty. Then `_orphans(client, group.id)`. Return:

   ```python
   {
       "instance": instance_name,
       "name": name,
       "group_id": group.id,
       "adlists": [_orphan_adlist_data(item) for item in adlists],
       "domains": [_orphan_domain_data(item) for item in domains],
   }
   ```

   Wrap in the module's standard `except HTTPException: raise / except Exception as exc:
   raise _api_error(exc) from exc`. This route reads only — it must not mutate.

4. **`GroupDeleteInput` gains the action and the Default-group confirmation (Revision 2).**

   ```python
   class GroupDeleteInput(BaseModel):
       name: str = Field(min_length=1, max_length=100)
       orphans: Literal["ignore", "delete", "reassign"] = "ignore"
       confirm_default: bool = False
   ```

5. **Rewrite `group_delete`.** Inside one `with _client(instance) as client:` session:

   1. `before = _find_group(client, payload.name)`.
   2. **(Revision 2) If `before.id == 0` and not `payload.confirm_default`:**
      `raise HTTPException(status_code=400, detail="Deleting the built-in Default group can "
      "leave this Pi-hole instance in an inconsistent state that commonly requires "
      "reinstalling Pi-hole to fully recover from. Confirm you understand this before "
      "proceeding.")`. This check runs before any orphan handling and regardless of
      `payload.orphans` — the risk is deleting group 0 itself, not what happens to its
      orphans. No Pi-hole call has been made yet at this point, so nothing needs rolling back.
   2b. **(Revision 3) If `before.id == 0` and `payload.orphans == "reassign"`:**
      `raise HTTPException(status_code=400, detail="Cannot reassign entries to the Default "
      "group while deleting it; choose 'delete' or 'ignore' instead.")`. Runs right after
      the 2. check, before any orphan computation or Pi-hole mutation — reassigning a group-0
      orphan to `[0]` and then deleting group 0 would silently re-strand it.
   3. If `payload.orphans == "ignore"`, skip to the group delete (today's path, unchanged
      audit record).
   4. Otherwise 409 if `before.id is None`, then recompute
      `adlists, domains = _orphans(client, before.id)` — **from this session's reads**, not
      from anything the client sent.
   4. Walk the entries sequentially, collecting
      `results: list[dict]` of `{"kind": "adlist"|"domain", "id": <address or domain>, "success": bool, "error": str | None}`:
      - `orphans == "delete"` → `client.delete_adlist(item.address)` /
        `client.delete_domain(item.domain, item.type, item.kind)`, auditing each as
        `adlist_delete` / `domain_delete` with an `after` of `{"cascade": f"group_delete:{payload.name}"}`
        so the cascade origin is greppable in `audit.log`.
      - `orphans == "reassign"` → `client.update_adlist(item, enabled=item.enabled, groups=[0])` /
        `client.update_domain(item, enabled=item.enabled, groups=[0])`, auditing each as
        `adlist_update` / `domain_update` with the same `cascade` marker in `after`. Note
        `enabled` is passed through from the read, not forced — reassigning must not
        silently re-enable a disabled entry.
      - Catch `PiholeAPIError` and bare `Exception` per entry, record the failure, and keep
        going, so one bad entry doesn't abandon the rest.
   6. If any adlist entry succeeded (either action), `mark_gravity_pending(instance_name)`
      — an adlist's group set changing is exactly the condition
      `adlist_update`/`adlist_delete` already treat as gravity-pending in `api.py`.
   7. **If any entry failed, do not delete the group.** Return HTTP 207-style semantics
      through a normal 200 body: `{"instance", "name", "deleted": False, "orphans": results,
      "gravity_pending": bool}` and let the frontend surface it as a warning. (A 5xx would
      lose the per-entry detail; the existing multi-instance add endpoints use the same
      "200 with a results array" convention.)
   8. All entries handled (or none to handle) → `client.delete_group(before.name)`, audit
      `group_delete` with `before` as today plus `{"orphans": payload.orphans, "orphan_count": len(results)}`
      in `after`, and return `{"instance", "name", "deleted": True, "orphans": results, "gravity_pending": bool}`.

6. **`frontend/src/api.ts`.** Add:

   ```ts
   export type OrphanAction = 'ignore' | 'delete' | 'reassign'

   export interface GroupOrphanAdlist {
     address: string
     comment: string
     enabled: boolean
   }

   export interface GroupOrphanDomain {
     domain: string
     type: 'allow' | 'deny'
     kind: 'exact' | 'regex'
     comment: string
     enabled: boolean
   }

   export interface GroupOrphans {
     instance: string
     name: string
     group_id: number
     adlists: GroupOrphanAdlist[]
     domains: GroupOrphanDomain[]
   }

   export interface OrphanResult {
     kind: 'adlist' | 'domain'
     id: string
     success: boolean
     error: string | null
   }
   ```

   and on the `api` object, `groupOrphans(instance, name)` hitting
   `/api/groups/${encodeURIComponent(instance)}/orphans?name=${encodeURIComponent(name)}`,
   plus a changed
   `deleteGroup(instance, name, orphans: OrphanAction = 'ignore', confirmDefault = false)`
   (Revision 2 adds the last parameter) sending `{ name, orphans, confirm_default: confirmDefault }`
   and typed
   `{ instance: string; name: string; deleted: boolean; orphans: OrphanResult[]; gravity_pending: boolean }`.

7. **`frontend/src/pages/GroupsPage.tsx` — the delete modal.** Replace the per-instance
   delete `Popconfirm` with a button that sets
   `deleting: { instance: string; row: GroupRow }` state and kicks off
   `api.groupOrphans(instance, row.name)`. Render a `Modal` titled
   `Remove "<name>" from <instance>?` containing:

   - while the preview is in flight, a `Skeleton`;
   - if the preview failed, an `Alert type="error"` with the message and the modal's OK
     button disabled — a delete must not proceed on an unknown orphan set;
   - if there are no orphans, `Typography.Text` "No block lists or domains are scoped only
     to this group." and a single confirm;
   - if there are orphans, an `Alert type="warning"` naming the counts, a `List` of the
     stranded entries (address / domain with its Allow-Deny and Exact-Regex tags), and a
     `Radio.Group` bound to an `action` state with the three options:
     - `ignore` — "Delete the group only (these entries will be left with no group)"
     - `reassign` — "Move them to the Default group, then delete the group" (default
       selection: the non-destructive one)
     - `delete` — "Delete these entries too, then delete the group"
     **(Revision 3)** When the target group's id is 0, the `reassign` option is not rendered
     (there is no valid reassign target — see Revision 3) and the default `action` is
     `ignore`. The `delete` option remains available.
   - **(Revision 2) if `row.instances[instance]?.id === 0`** — deleting the built-in Default
     group — render this section regardless of the orphan branches above (the risk is
     independent of orphan count, including zero): a distinct `Alert type="error"` reading
     "Deleting the Default group can leave this Pi-hole instance in an inconsistent state.
     You may be able to recover it by recreating the group, naming it 'Default', and
     reassociating it with all hosts and rules. This is not guaranteed to work, and you may
     end up needing to reinstall." and a `Checkbox` bound to a new `acknowledgedDefaultRisk` state — "I
     understand this may corrupt this Pi-hole instance and could require reinstalling it."
     This gate is independent of and additional to the orphan `Radio.Group` above; both
     must be satisfied before OK is enabled when the target is the Default group.
   - OK button text follows the selection; `danger` when the selection is `delete` **or**
     the target is the Default group. OK is disabled while the preview is loading or failed
     (as today), and additionally, when the target is the Default group, until
     `acknowledgedDefaultRisk` is checked.

   On confirm, call
   `api.deleteGroup(instance, name, action, row.instances[instance]?.id === 0 && acknowledgedDefaultRisk)`.
   If `deleted` is false or any `orphans` result has `success: false`, `message.warning`
   naming the failed ids and whether the group survived; otherwise `message.success`.
   Reload with `load()` either way, and reset `acknowledgedDefaultRisk` to `false` when the
   modal closes (opening it again for a different group must not carry the checkbox over).
   Note the page reads `working` for its busy state — reuse it.

8. **Rebuild the bundle.** `cd frontend && npm run build && npm run lint`, commit
   `app/static/ui/`.

9. **Docs.** design.md §5.6: add a bullet describing that deleting a group first reports
   the adlists and domains scoped solely to it and offers to delete or reassign them, that
   the set is recomputed server-side at write time, and that a cascade touching an adlist
   marks gravity pending. Move the group-deletion item in `docs/potential-features.md` from
   §1 to the shipped list, citing plan 0009.

## Contracts

### `GET /api/groups/{instance_name}/orphans?name=<group name>` (new)

Response:

```json
{
  "instance": "pihole1",
  "name": "Kids",
  "group_id": 3,
  "adlists": [{ "address": "https://lists.example/block.txt", "comment": "", "enabled": true }],
  "domains": [{ "domain": "ads.example.com", "type": "deny", "kind": "exact", "comment": "", "enabled": true }]
}
```

Errors: 404 instance not found / group not found on this instance; 409 no stored credential,
or the group has no id on this instance; 502/503 via `_api_error`.

`api.ts`: `groupOrphans: (instance: string, name: string) => Promise<GroupOrphans>`.

### `DELETE /api/groups/{instance_name}` (changed)

Request body — `orphans` and `confirm_default` (Revision 2) are new and optional:

```json
{ "name": "Kids", "orphans": "ignore" | "delete" | "reassign", "confirm_default": false }
```

Response — `deleted`, `orphans`, and `gravity_pending` are new; `instance` and `name`
unchanged:

```json
{
  "instance": "pihole1",
  "name": "Kids",
  "deleted": true,
  "orphans": [{ "kind": "adlist", "id": "https://lists.example/block.txt", "success": true, "error": null }],
  "gravity_pending": true
}
```

`deleted: false` means orphan handling hit at least one failure and the group was
deliberately left in place. Old callers that omit `orphans` get `"ignore"` and today's
behaviour, with `orphans: []` and `deleted: true` added to the response.

**(Revision 2)** If the target group's id is `0` (Pi-hole's built-in Default group) and
`confirm_default` is not `true`, the request is rejected with **400 Bad Request** naming the
risk (see Step 5) before any Pi-hole call is made. This is a data-dependent check — whether
the named group happens to be id 0 — not a request-shape validation, so it lives in the route
body rather than a Pydantic validator. Applies regardless of `orphans`.

**(Revision 3)** If the target group's id is `0` and `orphans` is `"reassign"`, the request
is rejected with **400 Bad Request** ("Cannot reassign entries to the Default group while
deleting it; ...") before any Pi-hole call is made, whether or not `confirm_default` is
`true` — reassigning a group-0 orphan to `[0]` then deleting group 0 would re-strand it.
`"delete"` and `"ignore"` on group 0 are still permitted (subject to the `confirm_default`
gate). Also a route-body data-dependent check.

`api.ts`: `deleteGroup(instance: string, name: string, orphans?: OrphanAction, confirmDefault?: boolean)`.

### Pi-hole client

N/A — no new Pi-hole endpoints. Uses `get_adlists`, `get_domains`, `delete_adlist`,
`delete_domain`, `update_adlist`, `update_domain`, `get_groups`, `delete_group`, all
existing.

### Config keys / DB

N/A — no new config key. Writes to the existing `pending-gravity.yml` through
`mark_gravity_pending`, which `app/routes/groups.py` does not import today and will.

## Tests

`tests/test_groups.py`, extending `FakeGroupsPiholeClient` with class-level `adlists` and
`domains` dicts keyed by instance name, `get_adlists` / `get_domains` /
`delete_adlist` / `delete_domain` / `update_adlist` / `update_domain` recorders, and
per-instance failure-injection sets (`fail_adlist_delete`, `fail_domain_update`). Reset all
of it in `setup_groups`, and monkeypatch `groups.mark_gravity_pending` onto a recording
list.

- `test_group_orphans_lists_only_solely_scoped_entries` — an adlist in `(3,)`, an adlist in
  `(0, 3)`, a domain in `(3,)`, and a domain in `(0,)`; asserts only the two `(3,)` entries
  come back.
- `test_group_orphans_returns_empty_when_nothing_is_scoped` — asserts `adlists == []` and
  `domains == []`, not an error.
- `test_group_orphans_unknown_group_returns_404`.
- `test_group_delete_ignore_leaves_entries_untouched` — default action; asserts no adlist or
  domain calls were made, the group is gone, and `deleted is True`.
- `test_group_delete_cascade_removes_orphans_before_the_group` — asserts the recorded call
  order has both orphan deletions before `delete_group`, and that a two-group entry was not
  touched.
- `test_group_delete_reassign_moves_orphans_to_default` — asserts `update_adlist` /
  `update_domain` were called with `groups=[0]`, that the group is then deleted, and that a
  disabled orphan stays disabled (`enabled=False` passed through, not forced true).
- `test_group_delete_marks_gravity_pending_only_when_an_adlist_changed` — parametrized over
  the two actions with (a) an adlist orphan and (b) a domain-only orphan; asserts the
  instance is marked pending in case (a) and not in case (b).
- `test_group_delete_keeps_group_when_an_orphan_fails` — injected failure on one orphan;
  asserts `deleted is False`, the group still exists on the fake instance, the failing
  entry's result carries `success: False` with a message, and the other entry still
  succeeded.
- `test_group_delete_recomputes_orphans_at_write_time` — the fake's `get_adlists` returns a
  different set on the second call than the first; asserts the deletion acted on the
  second (write-time) read.
- `test_group_delete_rejects_group_without_id_for_cascade_actions` — group with `id=None`
  and `orphans="delete"` raises 409, while `orphans="ignore"` still succeeds.
- `test_group_delete_input_rejects_unknown_orphan_action` — `orphans="nuke"` raises
  `ValidationError`.
- `test_group_delete_rejects_default_group_without_confirmation` (Revision 2) — a group
  named "Default" with `id=0`; `group_delete(..., GroupDeleteInput(name="Default"))` (the
  default `confirm_default=False`) raises `HTTPException` 400, and asserts no Pi-hole call
  was made at all (`delete_group`, `get_adlists`, `get_domains` all uncalled) — the check
  fires before any orphan work begins.
- `test_group_delete_allows_default_group_with_confirmation` (Revision 2) — same setup with
  `confirm_default=True` and `orphans` in `{"ignore", "delete"}` (not `"reassign"` — see the
  Revision 3 test below); asserts the group is deleted normally (orphan handling, if any,
  proceeds exactly as for any other group).
- `test_group_delete_default_check_ignores_orphans_choice` (Revision 2) — parametrized over
  all three `orphans` values with `id=0` and `confirm_default=False`; asserts all three raise
  the same 400 — the gate is independent of the orphan action chosen.
- `test_group_delete_confirm_default_is_a_no_op_for_non_default_groups` (Revision 2) — a
  non-Default group (nonzero id) deleted with `confirm_default=False` (the default) succeeds
  normally — the new field only matters when it's actually needed.
- `test_group_delete_rejects_reassign_for_default_group` (Revision 3) — a group with `id=0`,
  `orphans="reassign"`, `confirm_default=True`; raises `HTTPException` 400 and asserts no
  Pi-hole call was made (`delete_group`, `update_adlist`, `update_domain` all uncalled). The
  rejection stands even with `confirm_default=True` — it is not the `confirm_default` gate.
- `test_group_delete_reassign_still_works_for_non_default_groups` (Revision 3) — a nonzero-id
  group with `orphans="reassign"` still moves orphans to `[0]` and deletes the group (this is
  really the existing `test_group_delete_reassign_moves_orphans_to_default`; assert it is
  unchanged by the Revision 3 guard).

## Invariants at risk

1. **One instance mutated at a time.** This is the invariant this plan gets closest to: a
   single delete now performs *many* writes. All of them go to the one instance named in the
   path, through one `PiholeClient` for one `Instance`, in a plain sequential `for` loop —
   no `_fanout`, no thread pool, no second instance resolved anywhere in `group_delete`.
   The Reviewer should specifically confirm `_fanout` is not used on this path and that
   `mark_gravity_pending` is called with `instance_name` only. The memory note that two
   simultaneous authenticated calls to one Pi-hole can race into a 401 also argues for the
   strictly sequential loop inside one session — nothing here may be parallelized.
2. **Diff before write.** The preview endpoint does not write, and the delete recomputes the
   orphan set from a fresh read inside the mutating session rather than trusting the
   preview the browser was shown. The user's confirm step lists the entries by name before
   anything fires.
3. **Gravity gated behind real changes.** `mark_gravity_pending` is called only when at
   least one *adlist* orphan was actually deleted or reassigned — not on a domain-only
   cascade, not on `ignore`, and not when every orphan attempt failed. Gravity itself is
   still only ever run by the user's explicit button on the Block Lists page; nothing here
   calls `update_gravity`.
4. **No background scheduler.** All of it is request-scoped, initiated by the delete modal.
5. **FastAPI owns credentials.** The preview and delete both use `_client(instance)` /
   `CredentialStore`. The new `api.ts` types carry instance names, group names, addresses,
   and domains — no URL, password, or session id.
6. **Outbound requests via `url_policy`.** No new outbound URL. The adlist addresses handled
   here are read back from Pi-hole and passed to `delete_adlist` / `update_adlist` as
   opaque identifiers; this app never fetches them, so `screen_adlist_url` is correctly not
   involved on the delete path (it guards *user-submitted* addresses at add time, which is
   unchanged).
7. **Every route behind auth middleware.** The preview route is registered on the existing
   `app/routes/groups.py` `APIRouter(prefix="/api")`; no exemption added.
8. **Compiled frontend committed.** Step 8.
9. **`main` is production.** Work lands on `feature/0009-group-deletion-orphans`.

## Acceptance criteria

- [ ] `GET /api/groups/{instance}/orphans?name=...` lists exactly the adlists and domains
      whose group set is only that group, and returns empty lists (not an error) when there
      are none.
- [ ] An entry belonging to the target group *and* another group never appears in the
      preview and is never modified by any of the three actions.
- [ ] `orphans: "ignore"` (and an omitted field) reproduces today's behaviour byte-for-byte
      apart from the added response fields.
- [ ] `orphans: "delete"` removes the stranded entries and then the group, in that order.
- [ ] `orphans: "reassign"` moves the stranded entries to group `[0]`, preserving each
      entry's `enabled` state, and then deletes the group.
- [ ] A failure while handling one orphan leaves the group in place, reports that entry in
      `orphans` with `success: false`, and still reports the entries that succeeded.
- [ ] Gravity is marked pending for that instance when and only when an adlist was
      changed; the Block Lists page then offers its usual explicit Update button.
- [ ] The Groups delete modal blocks confirmation while the preview is loading or failed,
      defaults to the non-destructive reassign option when orphans exist, and shows the
      affected entries by name.
- [ ] The audit log contains one entry per orphan touched, each carrying the
      `cascade: "group_delete:<name>"` marker, plus the `group_delete` entry.
- [ ] **(Revision 2) Deleting the Default group (id 0) without `confirm_default: true`
      always fails with a 400, before any Pi-hole call is made, regardless of the chosen
      `orphans` action** — including when there are zero orphans to handle. Deleting any
      other group is entirely unaffected by the new field.
- [ ] **(Revision 2)** The delete modal shows the Default-group warning and disables OK
      until the acknowledgment checkbox is checked, whenever — and only when — the target
      group's id is 0 on that instance.
- [ ] **(Revision 3)** `orphans: "reassign"` on the Default group (id 0) is rejected with a
      400 before any Pi-hole call, even with `confirm_default: true`. `reassign` on every
      other group is unchanged. The delete modal does not offer the `reassign` radio option
      when the target group's id is 0, and defaults `action` to `ignore` in that case.
- [ ] **Live verification against a real instance:** on pihole1, create a throwaway group,
      scope one adlist and one domain solely to it, delete the group with `reassign`, and
      confirm in the Pi-hole admin UI that both entries now sit in Default and are still in
      their original enabled state. Repeat with `delete` and confirm both entries are gone.
      Also confirm a sequential cascade of several orphans (not just one of each) completes
      without any transport-level hiccup — the kind plan 0008 found for CNAME writes; none
      is expected here (see Revision 1), but this plan has not exercised the exact
      shape of "several sequential adlist/domain writes in one group-delete session" live
      before. Separately (Revision 2), confirm the Default-group gate end to end: attempting
      to delete the Default group through the UI without checking the acknowledgment box
      must be impossible (OK stays disabled); with the box checked, the backend must accept
      `confirm_default: true` and actually proceed with the deletion. **Given the confirmed
      real risk (Revision 2), do not carry this step through to completion on pihole1** — the
      app-level rejection (`confirm_default` omitted/false) and the enabled/disabled state of
      the modal's OK button are what need confirming; stop there rather than actually
      deleting a real instance's Default group to observe Pi-hole's own failure mode, which
      is exactly the outcome this gate exists to prevent accidentally triggering. Record all
      results in this plan file before merge.
- [ ] `./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
- [ ] design.md §5.6 and docs/potential-features.md updated.

## Implementation verification — 2026-09-07

- `./.venv/bin/pytest`: 293 passed, including 36 group tests covering orphan
  previews, ignore/delete/reassign, ordering, partial failures, fresh reads, gravity
  marking, audit markers, and the Default-group confirmation/reassign guards.
- `cd frontend && npm run build && npm run lint`: both exited 0; production bundle
  regenerated. Vite reports its empty React chunk and large-chunk warnings. ESLint
  reports one existing missing-effect-dependency warning in unchanged `src/App.tsx`;
  no errors or warnings in the changed frontend files.
- `git diff --check`: clean. Implementation files match the Files list; this
  verification record is added here as required by the acceptance checklist.
- Live verification (Codex run): pending — `pihole1` is not configured in that
  environment (`_instance` returned 404), so Codex attempted no live Pi-hole mutation.

### Live verification — 2026-09-07 (Kyrnveil, against `lab-pihole`)

Ran end to end against the real `lab-pihole` instance; all four parts confirmed:
- **reassign** — throwaway group with an adlist and a domain scoped solely to it;
  deleting with `reassign` moved both into Default with their enabled state
  preserved, then deleted the group.
- **delete** — same setup; deleting with `delete` removed both orphan entries, then
  the group.
- **multi-entry cascade** — several orphans in a single delete (not just one of
  each) completed with no transport-level hiccup (the CNAME-write failure mode plan
  0008 found does not occur here — see Revision 1).
- **Default-group gate** — the modal's OK button stays disabled until the
  acknowledgment checkbox is checked, and the backend rejects a request with
  `confirm_default` omitted/false with a 400. Per the Revision 2 acceptance note,
  deletion of `lab-pihole`'s real Default group was **not** carried through.
