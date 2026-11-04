# 0048 — Assign groups on create; hide app fields from password managers

Status: in-progress          <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0048-create-with-groups-and-password-manager-ignore
Author: Kyrnveil
Date: 2026-11-03

## Problem

Two unrelated papercuts in the add/edit flows.

1. The Add modals on Domains, Adlists and Clients cannot set groups. Every new record lands
   in group 0 (Default), because `PiholeClient.add_domain`, `add_adlist` and `add_client`
   hardcode `"groups": [0]`. The user must create the record, then open "Edit groups" on
   each instance. That is two writes per instance and a window where the record filters
   the wrong clients.
2. Bitwarden treats app fields as credential fields. It opens its inline menu with
   generated passwords on inputs such as the Add modal's Instances select, and it offers
   "Save login?" after non-login forms submit. Bitwarden ignores `autocomplete="off"`. Its
   opt-out is a `data-bwignore` attribute on each field.

## Scope

In:
- Optional `groups` (group names) on `POST /api/domains`, `POST /api/adlists`,
  `POST /api/clients`. The backend resolves names to IDs per instance and creates the
  record with those groups in one Pi-hole write.
- A Groups multi-select in the Add modals of `DomainsPage`, `AdlistsPage`, `ClientsPage`.
  It defaults to `Default` and offers only groups present on every selected instance.
- "Copy here" prefills Groups from the source record's groups.
- A frontend helper that stamps password-manager ignore attributes on every
  `input`/`textarea`/`select` in the document, including AntD modal portals, except
  inside the sign-in form.
- The Instances page app-password field is ignored too (user decision).

Out:
- Creating missing groups on an instance during add. A name missing on an instance fails
  that instance's add.
- Changes to the Edit groups modals, bulk actions, Groups page, or drift sync.
- Groups on the DNS Hosts/CNAMEs pages (Pi-hole local DNS records have no groups).
- A guarantee that Bitwarden's "Save login?" prompt never appears on the Instances form.
  That form has a real `type="password"` field, and Bitwarden's save heuristics may still
  fire on it despite `data-bwignore`.

## Approach

### Groups on create

The browser sends group **names**, not IDs. Group IDs are per-instance, so one ID list
cannot target several instances. Names are unique per Pi-hole instance, and the Groups
page already aligns instances by name (`GroupRow.name`).

Per instance, inside the existing sequential loop, the route:

1. opens the client,
2. calls `client.get_groups()` and resolves names with a new pure function
   `group_ids_for_names(groups, names)` in `app/pihole_client.py`,
3. calls `add_*(…, groups=ids)` so Pi-hole creates the record with its final groups.

A name missing on an instance raises `PiholeAPIError("Selected group not found on this
instance")`. That is a fixed string, so the existing `except PiholeAPIError` branch
reports it as that instance's error and the loop continues. No write happens for that
instance.

Omitting `groups` keeps today's behaviour (`[0]`) with no extra `get_groups()` call, so
older callers stay valid. An explicit list must have at least one name. A record with no
groups filters nothing, and an empty selection at create time is more likely an accident.

The frontend computes the option list from `api.listGroups()` data it already loads:
the names whose `GroupRow.instances[name].id` is non-null on every selected instance.
When the instance selection changes, selected names that left the option list are
dropped. The backend check stays authoritative because group data can be stale.

Rejected alternatives:
- Sending a per-instance `{instance: [ids]}` map. It trusts stale browser data, and a
  group deleted and recreated since page load would get the wrong ID.
- Add, then PATCH groups. That is two writes per instance and recreates the window the
  user complained about.

### Password-manager ignore

Bitwarden collects fields with `input:not([data-bwignore])`, matched by presence. AntD
does not forward arbitrary attributes to the inner `<input>` of `Select`, and modals
render into `document.body` outside `#root`. Per-component props would therefore miss
fields and rot as pages are added.

A new module `frontend/src/passwordManagerIgnore.ts` stamps attributes on the DOM:

- `data-bwignore="true"` (Bitwarden)
- `data-1p-ignore="true"` (1Password)
- `data-lpignore="true"` (LastPass)

It skips any element inside `[data-password-manager="allow"]`. The sign-in form sits in
such a wrapper, so sign-in autofill keeps working.

`main.tsx` starts a `MutationObserver` on `document.body` before React renders. Observer
callbacks run as microtasks right after each DOM change. Bitwarden debounces its field
collection behind a timer, so new fields carry the attribute before Bitwarden reads them.
The manual Bitwarden check in Acceptance criteria confirms this in practice.

Rejected alternatives:
- `autocomplete="off"` everywhere. Bitwarden ignores it.
- Per-field `data-bwignore` props. They cannot reach AntD `Select`'s inner input and need
  every future field to remember them.

Revision 1 (manual Bitwarden check failed): The first pass shipped the attributes, and
Bitwarden still offered logins and generated passwords on the Instances select. Two facts
in current sources explain it.

- Bitwarden now honors `data-bwignore` only when the user enables an opt-in setting,
  which is off by default (`honorBitwardenIgnoreAttribute` in
  `collect-autofill-content.service.ts`). The attribute does nothing for most users.
- AntD 6's Select renders its inner input as `type="text"` with a hardcoded
  `autocomplete="new-password"` (`@rc-component/select/es/SelectInput/Input.js`). No
  Select prop overrides it. Bitwarden's `isLikePasswordField` treats any text input with a
  `new-password` token as a password field. So every Select looks like a new-password
  field, which triggers the generator and, on submit, the save-login prompt.

The fix rewrites the misleading token at the DOM level. The same stamping helper sets
`autocomplete="off"` on any non-`password` input whose `autocomplete` contains
`new-password`, outside the allow wrapper. React sets that attribute only on mount and
when the prop changes, and rc-select's value is constant, so the rewrite sticks. The
`data-*` attributes stay because they help users who opt in and other managers.
`autocomplete="off"` alone does not stop Bitwarden from recognizing a field, but here it
only has to remove the false password signal.

Revision 1 does not fix the Instances app-password field, which is a real
`type="password"` input. Bitwarden may still offer to generate or save there. That residual
was already accepted in Scope.

## Files

- `app/pihole_client.py` — `group_ids_for_names`; `groups` kwarg on `add_domain`,
  `add_adlist`, `add_client`.
- `app/routes/domains.py` — `DomainAddInput.groups`; resolve and pass groups in
  `domain_add`; audit the real groups.
- `app/routes/api.py` — `AdlistAddInput.groups`; same in `adlist_add`.
- `app/routes/clients.py` — `ClientAddInput.groups`; same in `client_add`.
- `frontend/src/api.ts` — `groups?: string[]` on `addDomain`, `addAdlist`, `addClient`.
- `frontend/src/groupOptions.ts` — new; shared pure helpers for the Groups select.
- `frontend/src/groupOptions.test.ts` — new; tests for those helpers.
- `frontend/src/pages/DomainsPage.tsx` — Groups field in Add modal; Copy-here prefill.
- `frontend/src/pages/AdlistsPage.tsx` — same.
- `frontend/src/pages/ClientsPage.tsx` — same.
- `frontend/src/passwordManagerIgnore.ts` — new; stamping helper and observer.
- `frontend/src/passwordManagerIgnore.test.ts` — new; tests with fake elements.
- `frontend/src/main.tsx` — start the observer before render.
- `frontend/src/App.tsx` — wrap the sign-in `Form` in
  `<div data-password-manager="allow">`.
- `tests/test_pihole_client.py`, `tests/test_domains.py`, `tests/test_api.py`,
  `tests/test_clients.py` — new tests; fakes gain `get_groups` and a `groups` kwarg.
- `app/static/ui/` — rebuilt bundle.

## Steps

1. **Pi-hole client.** In `app/pihole_client.py`:
   - Add `def group_ids_for_names(groups: Sequence[Group], names: Sequence[str]) -> list[int]`.
     It returns IDs in the order of `names`, deduplicated. Match names exactly
     (case-sensitive, as Pi-hole stores them). Skip groups whose `id is None`. Raise
     `PiholeAPIError("Selected group not found on this instance")` for any unmatched name.
   - Change `add_domain(self, domain, type, kind, comment="", groups: Sequence[int] = (0,))`,
     `add_adlist(self, address, comment="", groups: Sequence[int] = (0,))` and
     `add_client(self, client, comment="", groups: Sequence[int] = (0,))`. Each sends
     `"groups": list(groups)` instead of `[0]`. Keyword-compatible with existing callers.
2. **Shared input field.** Each of `DomainAddInput`, `AdlistAddInput`, `ClientAddInput`
   gains:
   ```python
   groups: list[str] | None = Field(default=None, min_length=1, max_length=100)
   ```
   with a `field_validator("groups")` that strips each name, rejects empty names and
   names over 100 characters with `ValueError("Group names must be 1-100 characters")`,
   and deduplicates while keeping order. Put the validator body in one module-level
   function in each route module, or import one shared function; either is fine as long
   as the three models behave identically.
3. **`domain_add`.** Inside the per-instance `with _client(...) as client:` block:
   ```python
   group_ids = [0] if payload.groups is None else group_ids_for_names(client.get_groups(), payload.groups)
   client.add_domain(payload.domain, payload.type, payload.kind, payload.comment, groups=group_ids)
   ```
   Add `"groups": group_ids` to the audit `after` dict. Loop stays sequential.
4. **`adlist_add`** (`app/routes/api.py`). Same pattern with `add_adlist`. Keep
   `mark_gravity_pending(name)` after the write. Add `"groups": group_ids` to the audit
   `after` dict.
5. **`client_add`** (`app/routes/clients.py`). Same pattern with `add_client`. Replace the
   audit's hardcoded `"groups": [0]` with `group_ids`.
6. **`api.ts`.** Add a trailing optional parameter `groups?: string[]` to `addDomain`,
   `addAdlist` and `addClient`. Include it in the JSON body only when defined, matching
   the `updateDomain` spread style.
7. **`groupOptions.ts`.** New module exporting:
   - `commonGroupNames(rows: GroupRow[], instances: string[]): string[]` — names whose
     `row.instances[instance]?.id != null` for every instance in `instances`, sorted with
     `Default` first, then alphabetically. Returns `[]` when `instances` is empty.
   - `sourceGroupNames(groupIds: number[], namesById: Record<number, string> | undefined): string[]`
     — maps IDs to names and drops IDs with no name.
   - `DEFAULT_GROUP = 'Default'`.
   Move the duplicated `groupNameMap` from the three pages into this module and import it.
8. **Add modals** (`DomainsPage`, `AdlistsPage`, `ClientsPage`). In each:
   - Add `groups: string[]` to the form values type.
   - Watch instances with `Form.useWatch('instances', form)` and compute
     `groupOptions = commonGroupNames(groupRows, watchedInstances ?? [])` during render.
   - Add, after the Instances item:
     ```tsx
     <Form.Item name="groups" label="Groups" rules={[{ required: true, message: 'Select at least one group' }]}
       extra="Only groups that exist on every selected instance are listed.">
       <Select mode="multiple" disabled={!groupOptions.length} options={groupOptions.map((name) => ({ value: name, label: name }))} />
     </Form.Item>
     ```
   - On the Instances `Select`'s `onChange`, prune `groups` to the new
     `commonGroupNames(...)` result with `form.setFieldValue`.
   - `showAdd` sets `groups`. A plain Add sets `[DEFAULT_GROUP]`. "Copy here" sets
     `sourceGroupNames(...)` from the first instance in `row.instances` that holds the
     record, using `groupNameMap(groupRows)[thatInstance]`, then filters to the options
     available for the target instance. If that leaves nothing, fall back to
     `[DEFAULT_GROUP]` when available.
   - `ClientsPage` loads `listGroups` already; `AdlistsPage` and `DomainsPage` do too. No
     new fetches.
   - Pass `values.groups` to `api.add*`.
   - Add the group names to the confirm dialog content, e.g. `` `${values.domain} → ${values.groups.join(', ')}` ``.
9. **`passwordManagerIgnore.ts`.** New module exporting:
   - `PASSWORD_MANAGER_IGNORE_ATTRIBUTES = ['data-bwignore', 'data-1p-ignore', 'data-lpignore'] as const`
   - `ALLOW_SELECTOR = '[data-password-manager="allow"]'`
   - `markIgnoredFields(root: ParentNode & Partial<Element>): void` — collects
     `input, textarea, select` from `root.querySelectorAll`, plus `root` itself when it
     matches. For each element not inside `ALLOW_SELECTOR` (`el.closest(...) === null`),
     sets each missing attribute to `"true"`.
   - `watchPasswordManagerIgnore(target: HTMLElement): () => void` — calls
     `markIgnoredFields(target)`, then observes `{ childList: true, subtree: true }` and
     calls `markIgnoredFields` on each added `Element` node. Returns a disconnect function.
10. **`main.tsx`.** Call `watchPasswordManagerIgnore(document.body)` before
    `createRoot(...).render(...)`. The observer lives for the page's lifetime, so the
    return value is not used.
11. **`App.tsx`.** Wrap the sign-in `<Form>` in `<div data-password-manager="allow">`.
    The existing `autoComplete="username"` / `"current-password"` props stay.
12. **Build.** `cd frontend && npm run build && npm run lint && npm test`; commit
    `app/static/ui/`.
13. **Revision 1 — neutralize `new-password` on non-password inputs.** In
    `markIgnoredFields`, for each field outside the allow wrapper that is an `input` whose
    `type` attribute is not `password`, and whose `autocomplete` attribute contains the
    token `new-password` (split on whitespace, case-insensitive), set `autocomplete` to
    `off`. Leave `type="password"` inputs and every other `autocomplete` value unchanged.
    Rebuild and commit `app/static/ui/`.

## Contracts

Backend request models (response shapes unchanged):

```python
class DomainAddInput(BaseModel):
    instances: list[str] = Field(min_length=1, max_length=100)
    domain: str = Field(min_length=1, max_length=1000)
    type: Literal["allow", "deny"]
    kind: Literal["exact", "regex"]
    comment: str = Field(default="", max_length=1000)
    groups: list[str] | None = Field(default=None, min_length=1, max_length=100)

class AdlistAddInput(BaseModel):
    instances: list[str] = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=2048)
    comment: str = Field(default="", max_length=1000)
    groups: list[str] | None = Field(default=None, min_length=1, max_length=100)

class ClientAddInput(BaseModel):
    instances: list[str] = Field(min_length=1, max_length=100)
    client: str = Field(min_length=1, max_length=253)
    comment: str = Field(default="", max_length=1000)
    groups: list[str] | None = Field(default=None, min_length=1, max_length=100)
```

`frontend/src/api.ts`:

```ts
addDomain: (instances: string[], domain: string, type: 'allow' | 'deny', kind: 'exact' | 'regex', comment: string, groups?: string[]) =>
  request<{ results: Array<{ name: string; success: boolean; error: string | null }> }>('/api/domains', {
    method: 'POST', body: JSON.stringify({ instances, domain, type, kind, comment, ...(groups ? { groups } : {}) }),
  }),
addAdlist: (instances: string[], address: string, comment: string, groups?: string[]) => …same pattern…
addClient: (instances: string[], client: string, comment: string, groups?: string[]) => …same pattern…
```

Per-instance result on a missing group:
`{"name": "<instance>", "success": false, "error": "Selected group not found on this instance"}`.

Audit `after` payloads for `domain_add`, `adlist_add` and `client_add` include
`"groups": [<resolved ids>]`.

No config keys or DB changes.

## Tests

Backend:
- `tests/test_pihole_client.py::test_group_ids_for_names_resolves_in_order` — names map
  to IDs in input order, duplicates collapse, `Default` resolves to `0`.
- `tests/test_pihole_client.py::test_group_ids_for_names_missing_name_raises` — raises
  `PiholeAPIError` with exactly `"Selected group not found on this instance"`.
- Extend the existing MockTransport test(s) that call `add_domain`, `add_adlist`,
  `add_client` to assert the request JSON carries `"groups": [0]` by default and the
  passed list when `groups=` is given.
- `tests/test_domains.py::test_domain_add_with_groups_resolves_per_instance` — two
  instances where `"Kids"` has different IDs (e.g. 3 and 7). Each instance's
  `add_domain` receives its own ID. The audit `after` carries those IDs.
- `tests/test_domains.py::test_domain_add_missing_group_fails_only_that_instance` —
  `"Kids"` exists on pihole1 only. pihole1 succeeds. pihole2 reports
  `"Selected group not found on this instance"` and no `add_domain` call happens for it.
- `tests/test_domains.py::test_domain_add_without_groups_uses_default_and_skips_lookup` —
  `groups` omitted → `add_domain` gets `[0]`, `get_groups` is not called.
- `tests/test_domains.py::test_domain_add_input_rejects_empty_groups` — `groups=[]` and
  `groups=[""]` raise `ValidationError`; `groups=[" Kids ", "Kids"]` normalises to
  `["Kids"]`.
- `tests/test_api.py::test_adlist_add_with_groups_resolves_per_instance` — as the domain
  test; also asserts `mark_gravity_pending` still fires for successful instances only.
- `tests/test_clients.py::test_client_add_with_groups_resolves_per_instance` — as the
  domain test; audit `after.groups` is the resolved list, not `[0]`.
- `tests/test_clients.py::test_client_add_missing_group_fails_only_that_instance`.

Fakes in the three route test files gain `get_groups()` (returning per-instance `Group`
lists and counting calls) and accept `groups=` on `add_*`. Existing assertions on
`added` tuples change only where the new `groups` element is appended.

Frontend (vitest, node environment — no new dependency):
- `groupOptions.test.ts` — `commonGroupNames` returns the intersection with `Default`
  first; returns `[]` for no instances; excludes groups whose `id` is null on any selected
  instance. `sourceGroupNames` maps IDs and drops unknown IDs.
- `passwordManagerIgnore.test.ts` — with hand-built fake elements (`closest`,
  `hasAttribute`, `setAttribute`, `matches`) and a fake root: fields outside the allow
  wrapper receive all three attributes; fields inside it receive none; a pre-existing
  attribute value is not overwritten; the root itself is stamped when it is an `input`.
- `passwordManagerIgnore.test.ts` (Revision 1) — the fake element gains `tagName` and
  `getAttribute`. A text input with `autocomplete="new-password"` ends with
  `autocomplete="off"`. A `type="password"` input with `new-password` keeps it. A text
  input with `autocomplete="username"` keeps it. A `new-password` text input inside the
  allow wrapper keeps it.

## Invariants at risk

1. **One instance mutated at a time.** The three add routes keep their sequential `for`
   loops. The new `get_groups()` read happens inside the same per-instance iteration,
   before that instance's single write.
2. **Diff before write.** Groups resolve against the instance's live group list at write
   time, not against browser data. A missing name skips the write for that instance.
   The add itself is unchanged in kind (create a new record).
3. **Gravity gated behind real changes.** `adlist_add` still calls `mark_gravity_pending`
   only after a successful add. No gravity call is added.
4. **No background scheduler.** The `MutationObserver` is a browser-side DOM listener. It
   never calls `/api` and never mutates a Pi-hole.
5. **Credentials never reach the browser.** Group names and IDs are not secrets. The
   Instances app-password field becomes ignored by password managers; its value still
   goes only to `/api`.
6. **`url_policy`.** No new outbound URLs. `get_groups()` uses the existing client.
7. **Auth middleware.** No new routes.
8. **Compiled frontend committed.** Step 12.
9. **`main` is production.** Work happens on the feature branch.

`PiholeAPIError` messages: the new message is a fixed string, so
`tests/test_error_messages.py` stays green.

## Acceptance criteria

- [ ] Adding a domain, adlist or client with Groups `Default, Kids` to two instances
      creates each record with both groups on both instances, in one write per instance.
- [ ] The Groups select lists only groups present on every selected instance, and
      dropping or adding an instance prunes the selection.
- [ ] A group missing on one instance fails that instance with
      "Selected group not found on this instance" and the other instances still succeed.
- [ ] "Copy here" prefills the source record's groups where they exist on the target.
- [ ] `POST` without `groups` still creates the record in group 0 (backward compatible).
- [ ] Audit entries for the three add actions record the resolved group IDs.
- [ ] In the built app, every `input`, `textarea` and `select` outside the sign-in form,
      including those inside open modals, carries `data-bwignore`, `data-1p-ignore` and
      `data-lpignore`. The sign-in fields carry none of them.
- [ ] (Revision 1) In the built app, no non-password input outside the sign-in form has
      `autocomplete` containing `new-password`. AntD Select inputs show
      `autocomplete="off"`.
- [ ] Manual check with Bitwarden installed: no inline menu or generated-password offer
      on the Add-modal Instances select, and no "Save login?" after adding a domain.
      Sign-in autofill still works. (The Instances app-password form may still trigger
      "Save login?"; that residual is accepted.)
- [ ] `./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` + `npm test` clean, `app/static/ui/` committed.
