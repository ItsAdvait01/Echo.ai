# Agent Collaboration and Code Ownership Rules

## Non-negotiable single-writer policy

**Every file has one write owner. An agent must not edit, reformat, rename, delete, or revert code owned by another agent - even if both agents work in the same folder or even if the requested fix is one line.**

1. Before coding, read the PRD, TRD, runbook, and your individual task card.
2. Work only on your named branch and paths in your ownership list.
3. If you need a change outside your owned paths, send a change request to the owner and wait for the owner/coordinator's patch. Do not make the change yourself.
4. Do not independently change API schemas, event shapes, response fields, or dependency manifests.
5. Shared contracts have one owner (A1); documentation and repository policy have one owner (Advait).
6. To contribute to a shared behavior, create a new module in your own area and request that the owner wires it in.
7. Never use force push, reset, or amend on another agent's branch. Never drop another person's commits while resolving a conflict.
8. If same-file contributions are unavoidable, only the designated file owner edits that file. Other agents submit a patch suggestion or pull request against the owner; they do not edit a supposedly separate “section” in parallel. This prevents line overlap and accidental destruction.
9. Coordinator is the sole integrator for `integration`; agents do not merge other agent branches.
10. Keep commits small, named, and scoped. Report exact files changed, checks actually run, blockers, assumptions, and commit hash.

## Change request template

```text
CHANGE REQUEST
Requester / agent:
File owner:
Path(s):
Required behavior:
Reason / blocking impact:
Proposed interface or patch:
Needed by:
```

## Ownership summary

| Agent | Owns |
|---|---|
| Advait | `docs/**`, `README.md`, repository policy and integration decisions. |
| A1 | `backend/app/main.py`, `backend/app/api/**`, `backend/app/core/**`, `backend/app/schemas/**`, `backend/pyproject.toml`. |
| A2 | `backend/app/memory/**`. |
| A3 | `backend/app/voice/**`. |
| A4 | `frontend/**`. |
| U1 | `backend/app/browser/**`. |
| U2 | `backend/app/learning/**`. |
| U3 | `backend/app/execution/planner.py`, `backend/app/execution/executor.py`. |
| U4 | `backend/app/execution/verifier.py`, `backend/app/execution/recovery.py`. |

Package `__init__.py` files and root scaffold are coordinator-created and remain coordinator-owned. U3 and U4 have different files despite a shared directory.

