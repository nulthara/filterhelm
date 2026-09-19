# NNNN — <title>

Status: draft            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/NNNN-slug
Author: <you>
Date: YYYY-MM-DD

## Problem

<What's wrong or missing, and why it matters. One paragraph.>

## Scope

In:
- <bullet>

Out:
- <explicitly not doing this>

## Approach

<The design decision and the alternatives rejected. Enough that the reviewer can judge
whether the result matches intent.>

## Files

- `path/to/file` — <why it changes>

## Steps

1. <Concrete, independently reviewable change. Name functions / endpoints / components / props.>
2. ...

## Contracts

<Any API request/response shape, config key, or DB change — written out exactly, both
sides (backend Pydantic model + `frontend/src/api.ts`). "N/A — <reason>" if none.>

## Tests

- `tests/test_<module>.py::test_<name>` — <what it asserts>

## Invariants at risk

<Which entries from ARCHITECT.md "Project invariants" this change gets near, and how the
plan keeps them intact. "None" if genuinely none.>

## Acceptance criteria

- [ ] <observable outcome the reviewer can check>
- [ ] `./.venv/bin/pytest` green
- [ ] (if frontend touched) `npm run build` + `npm run lint` clean, `app/static/ui/` committed
