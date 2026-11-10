# A2 workflow memory interface

Owner: A2. Consumers: A1 (API/routes), U3 (planner/executor), U4 (verification).
Storage: SQLite via SQLAlchemy (durable). A temporary in-memory adapter implements
the same interface and is selected only with `ECHO_MEMORY_BACKEND=memory`.

## Integration

```python
from app.memory.factory import get_repository

repo = get_repository()          # process singleton, durable SQLite by default
```

Do not import the SQLite or in-memory classes directly; use the factory so the
adapter can be swapped without changing call sites.

Configuration:

| Variable | Default | Meaning |
|---|---|---|
| `ECHO_MEMORY_BACKEND` | `sqlite` | `sqlite` or `memory` (temporary, non-durable). |
| `ECHO_MEMORY_DB` | `echo.db` (relative to the API working directory) | SQLite file path, ignored for `memory`. |

There is no silent SQLite-to-memory fallback: if SQLite cannot start, the factory
raises so the API never reports "saved" for data that was not persisted.

## Methods

Workflow lifecycle (`Workflow` from `app.schemas.contracts`):

| Method | Behavior |
|---|---|
| `save_draft(workflow)` | Stores an unapproved draft as-is for review (incomplete steps are allowed). Raises `UnapprovedWorkflowError` if `workflow.approved`. |
| `get_draft(workflow_id)` | Any approval state, for the review screen. |
| `approve(workflow_id)` | Flips an existing draft to approved and returns it. Refuses approval with no steps or duplicate step orders. |
| `reject(workflow_id)` | Keeps the record but marks it unapproved, so it is never runnable. |
| `save_approved(workflow)` | Insert/update an already-approved workflow. Raises `UnapprovedWorkflowError` when `approved` is false; also requires non-empty, unique step orders. |
| `get(workflow_id)` | Approved workflow only; `None` for missing, draft, or rejected ids. |
| `list_approved()` | Approved workflows ordered by `created_at`; this is the executable memory set. |
| `list_all()` | Every stored workflow, for library/review views. |

Execution progress:

| Method | Behavior |
|---|---|
| `create_run(workflow_id, run_id=None)` | Starts an `EXECUTING` run with `last_verified_step=0`. Raises `UnapprovedWorkflowError` unless the workflow is approved. |
| `get_run(run_id)` | `ExecutionRun` or `None`. |
| `list_runs(workflow_id=None)` | Newest first. |
| `set_run_state(run_id, state, ended_at=None)` | Records pause/stop/completion; `COMPLETED`/`FAILED` stamp `ended_at`. |
| `record_verified_progress(run_id, step_order=..., verified=..., verification=...)` | Stores a `VerifiedCheckpoint` and advances `last_verified_step` only for verified results. |
| `get_progress(run_id)` | `ExecutionProgress` = run + ordered verified checkpoints. |
| `get_latest_progress(workflow_id)` | Latest run progress for a workflow, or `None`. |

`record_verified_progress` invariants:

* `verified` must be `True` (the verifier's outcome), otherwise `UnverifiedProgressError`.
* `verification` evidence is mandatory and stored with the checkpoint.
* Evidence containing `verified: false` or `passed: false` is refused.
* Step order must exist on the workflow, otherwise `MemoryValidationError`.
* Repeated reports for the same step are idempotent; a lower step order never
  moves `last_verified_step` backwards.
* Attempted-but-unverified steps are never recorded as checkpoints.

Exceptions live in `app.memory.errors`: `MemoryError`, `MemoryValidationError`,
`WorkflowNotFoundError`, `RunNotFoundError`, `UnapprovedWorkflowError`,
`UnverifiedProgressError`.

## Suggested route wiring (A1)

| Route | Memory calls |
|---|---|
| `POST /api/workflows/{id}/approve` | `approve(id)` (or `save_draft` then `approve`) |
| `GET /api/workflows` | `list_approved()` |
| `GET /api/workflows/{id}` | `get(id)` → 404 when `None` |
| `POST /api/executions` | `create_run(workflow_id)` |
| `GET /api/executions/{run_id}` | `get_progress(run_id)` |
| `POST /api/executions/{run_id}/pause` / `stop` | `set_run_state(run_id, AgentState.PAUSED/...)` |
| Verify step (U4) | `record_verified_progress(...)` with the verifier's result |

## Change requests to A1 (contracts owner)

1. `ExecutionRun` has `last_verified_step` but no checkpoint list. A2 returns
   `ExecutionProgress` from `app.memory.models` (`run` + `verified_checkpoints`).
   If A1 wants a single response model in `contracts.py`, add
   `VerifiedCheckpoint` and `ExecutionProgress` there and A2 will consume them.
2. Workflows have no `approved_at`/`rejected_at` or `updated_at`; approval
   history is currently limited to the `approved` flag. Needed only if the
   review UI must show when approval happened.
3. `Workflow` has no "rejected" terminal status distinct from unapproved. A2
   treats rejected as `approved=False`, which keeps it non-runnable.

## Stored data and privacy

Only workflow structure (name, description, trigger phrases, semantic step
targets, expected results, uncertainty notes), approval state, run state, and
verification summaries are stored. Passwords, tokens, payment values, raw page
content, and unrelated browsing history are not part of any model or table.
