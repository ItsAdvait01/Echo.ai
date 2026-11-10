# Technical Requirements Document (TRD)

## TRD index

1. [Architecture](#1-architecture)
2. [Technology decisions](#2-technology-decisions)
3. [Repository and file ownership](#3-repository-and-file-ownership)
4. [Shared contracts](#4-shared-contracts)
5. [Service boundaries](#5-service-boundaries)
6. [Voice interface](#6-voice-interface)
7. [UI screen and component index](#7-ui-screen-and-component-index)
8. [Workflow data model](#8-workflow-data-model)
9. [Privacy and action safety](#9-privacy-and-action-safety)
10. [Errors, status, and verification](#10-errors-status-and-verification)
11. [Build and integration constraints](#11-build-and-integration-constraints)
12. [Related documents](#12-related-documents)

## 1. Architecture

```text
React + TypeScript UI
  Voice command / Teach / Review / Workflow Library / Execution Monitor
            | HTTP; WebSocket only if time permits
            v
FastAPI application (single process)
  API + session lifecycle + shared Pydantic contracts
       |                       |
       v                       v
Teach pipeline              Execute pipeline
Playwright observer         workflow retrieval
event processor             planner -> allowlisted executor -> verifier
interpreter/learner                    |
       |                               |
       +----------- SQLite ------------+
            Voice adapter: Gnani STT/TTS or fallback
```

All agents contribute modules to one application; they are not product microservices. Shared files have a single owner. Contributors request changes from that owner rather than editing the same file.

## 2. Technology decisions

| Layer | Choice | Constraint |
|---|---|---|
| UI | React + TypeScript + Vite; Tailwind | A4 owns UI files. |
| API | Python + FastAPI | A1 owns app/routes/contracts. |
| Validation | Pydantic | Shared schema changes go through A1. |
| Browser | Playwright | U1 owns observation; U3/U4 own execution and verification files. |
| Voice | Gnani Prisma STT + Timbre TTS, subject to API access | A3 owns adapter; mock/text fallback is explicitly identified. |
| Persistence | SQLite + SQLAlchemy planned | A2 owns memory adapter and migrations/schema. |
| Real-time | WebSocket if time permits | Otherwise use ordinary HTTP/polling. |
| Version control | Git + GitHub | Main is stable; integration is the shared merge target. |

Do not add microservices, multiple databases, vector databases, or a production multi-agent framework during the MVP.

## 3. Repository and file ownership

### 3.1 Ownership map

| Owner | Exclusive write ownership |
|---|---|
| Advait / coordinator | `docs/**`, `README.md`, `.gitignore`, `.env.example`, integration policy, root-level coordination files. |
| A1 - Backend foundation/API | `backend/app/main.py`, `backend/app/api/**`, `backend/app/core/**`, `backend/app/schemas/**`, `backend/pyproject.toml`. |
| A2 - Workflow memory | `backend/app/memory/**`. |
| A3 - Gnani voice | `backend/app/voice/**`. |
| A4 - Frontend | `frontend/**`. |
| U1 - Browser observer | `backend/app/browser/**`. |
| U2 - Workflow learner | `backend/app/learning/**`. |
| U3 - Planner/executor | `backend/app/execution/planner.py`, `backend/app/execution/executor.py`. |
| U4 - Verification/recovery | `backend/app/execution/verifier.py`, `backend/app/execution/recovery.py`. |

The coordinator creates package `__init__.py` files and owns them. U3 and U4 may work in the same `execution/` directory but must not edit one another's files. If a shared file becomes necessary, its listed owner remains the only writer.

### 3.2 Single-writer and change-request rule

1. An agent may edit only its owned paths. This includes small fixes, imports, formatting, and “just one line” changes.
2. Never change or revert code authored by another agent, even when both tasks seem to require a change to the same file.
3. If another file needs a change, open a concise change request to that file's owner: file/path, desired behavior, reason, proposed diff or interface, and urgency.
4. The owner decides and applies the change on their branch, or asks the coordinator to make an integration-only change.
5. If multiple agents need behavior from one file, add a new owned module behind a published interface; the owner wires it in.
6. Do not cherry-pick or copy another agent's unmerged changes into your branch. The coordinator merges reviewed branches into `integration` in dependency order.
7. Never force-push shared branches or reset another agent's commits. Resolve conflicts with the file owner; preserve both changes unless the coordinator explicitly rejects one.
8. Report blocker/interface requests immediately. A deadline is not permission to overwrite another agent's work.

## 4. Shared contracts

`backend/app/schemas/contracts.py` is the source of truth, owned exclusively by A1. Other agents consume it and request changes through A1.

### 4.1 Initial schema names

- `AgentState`: idle, listening, learning, processing, awaiting_approval, executing, waiting_for_confirmation, paused, completed, failed.
- `BrowserEvent`: id, session_id, timestamp, event_type, page_url, page_title, target_summary, safe_value; sensitive values omitted.
- `WorkflowStep`: id, order, action_type, target, expected_result, risk.
- `WorkflowDraft`: name, description, trigger_phrases, steps, uncertainty_notes.
- `Workflow`: draft fields plus id, approved status, created_at.
- `ExecutionRun`: id, workflow_id, state, last_verified_step, timestamps.
- `StatusEvent`: event type, state, run/session id, message, optional verification result.

### 4.2 Proposed HTTP route set

| Method + route | Owner | Purpose |
|---|---|---|
| `GET /api/health` | A1 | Health/readiness. |
| `POST /api/voice/command` | A1 + A3 | Receive transcript/voice intent and return normalized action. |
| `POST /api/learning/start` | A1 + U1 | Explicitly start observation session. |
| `POST /api/learning/{session_id}/stop` | A1 + U1/U2 | Stop capture and request interpretation. |
| `GET /api/learning/{session_id}/events` | A1 + U1 | Inspect filtered event trace. |
| `POST /api/workflows/{workflow_id}/approve` | A1 + A2 | Approve and persist workflow. |
| `GET /api/workflows` and `/api/workflows/{id}` | A1 + A2 | List/read approved workflows. |
| `POST /api/executions` | A1 + U3 | Start/resume a selected workflow. |
| `POST /api/executions/{run_id}/pause` / `stop` | A1 + U3/U4 | Pause or safely stop a run. |
| `GET /api/executions/{run_id}` | A1 + A2/U4 | Read run status and verified progress. |

Exact request/response fields are frozen by Advait and A1 before agents implement integration. Do not infer or silently redefine payloads from this table.

## 5. Service boundaries

- **A1 API/orchestration:** request validation, route wiring, session/run lifecycle, shared schemas.
- **A2 memory:** persistence interface for workflows and run/checkpoint records.
- **A3 voice:** speech input/output adapter and explicit mock adapter.
- **A4 frontend:** interaction, status, workflow review/library, execution monitor.
- **U1 observer:** explicit Playwright session and normalized browser event capture.
- **U2 learner:** distinguish observed facts from inferred intent; produce structured draft.
- **U3 planner/executor:** select next action and execute only allowlisted actions.
- **U4 verifier/recovery:** verify expected state, preserve last good checkpoint, bound retries, stop safely.

## 6. Voice interface

Voice path: capture utterance -> Gnani Prisma transcript -> normalized intent -> API action -> status response -> Gnani Timbre spoken reply. If real credentials/access are unavailable, A3 provides a clearly labeled mock or text-command adapter. Never hardcode secrets or claim real voice integration without a successful smoke test.

Intents: `teach.start`, `teach.stop`, `workflow.execute`, `execution.pause`, `execution.stop`, `status.get`. UI buttons/text fallback must invoke equivalent backend contracts.

## 7. UI screen and component index

### Screens

| ID | Screen | Required content/actions |
|---|---|---|
| UI-01 | Home | Agent state, voice input, Teach and Execute entry points, saved workflow summary. |
| UI-02 | Teach session | Explicit consent, active-site/session indication, observation count, pause/stop. |
| UI-03 | Workflow review | Name, ordered step list, expected outcomes, uncertainty, Approve/Reject. |
| UI-04 | Workflow library | Approved workflows, triggers, last verified checkpoint, select/delete. |
| UI-05 | Execution monitor | Current step, verified timeline, pause/stop, safe failure/confirmation message. |
| UI-06 | Settings/permissions | Voice adapter status, permission/privacy explanation, fallback status. |

### Components and interaction rules

`AgentStatusBadge`, `VoiceCommandBar`, `TeachConsentPanel`, `ObservationControls`, `EventSummary`, `WorkflowDraftReview`, `ApprovalActions`, `WorkflowList`, `ExecutionTimeline`, `CheckpointBadge`, `ConfirmationDialog`, `RecoveryMessage`, `ConnectionStatus`.

State labels must remain visible as text: Idle, Listening, Learning, Processing, Awaiting Approval, Executing, Waiting for Confirmation, Paused, Completed, Failed. Stop must be easy to reach; approval must be explicit; attempted steps must be visually distinct from verified steps. Voice is primary but never the only feedback channel.

## 8. Workflow data model

Workflow memory contains task name/description, trigger phrases, approval status, ordered supported actions, target description, preconditions, expected results, and recovery guidance. Execution records contain workflow ID, run state, last verified step, timestamps, and verification summaries. Never persist password values, tokens, payment data, or unrelated browsing history.

## 9. Privacy and action safety

- Observe only after explicit user authorization for the controlled browser session.
- Stop collection on pause/stop; filter password/payment values before storage or model calls.
- Do not store credentials in workflow memory or ask users to speak passwords.
- LLM output is untrusted structured proposal. Validate schema, action type, target, and permission before execution.
- No arbitrary generated Python/JavaScript, blind coordinate replay, or silent workflow overwrite.
- Require confirmation for sending, purchasing, deleting, submitting, or account/security changes.
- Do not automate graded assessments or falsely represent course completion.

## 10. Errors, status, and verification

Errors should carry stable `code`, user-readable `message`, and `retryable` flag. Missing/ambiguous locators pause execution. Verification failure must not advance checkpoint. Voice API failure exposes fallback; persistence failure must not report “saved”. Logs include safe request/session/run IDs, event/state types, and verification outcome, never secret values or full page content by default.

## 11. Build and integration constraints

- Keep contracts stable after foundation block; change them only through A1/Advait review.
- Each branch starts from the same `integration` commit and only touches owned files.
- Use a single FastAPI app and SQLite. WebSockets only if the core path is integrated.
- Pin/add dependencies only through the owning manifest owner; communicate before any shared dependency edit.
- Branch checks and smoke tests are run by the owner before requesting integration.

## 12. Related documents

- [PRD](PRD.md): user/problem scope, priorities, and acceptance criteria.
- [RUNBOOK](RUNBOOK.md): three-hour schedule, branch setup, integration, demo, fallback.
- [WORKFLOW](WORKFLOW.md): Teach/Execute steps, states, and stop rules.
- [AGENT_RULES](AGENT_RULES.md): global collaboration rules and no-overwrite policy.
- [Agent context cards](agents/): assignment, paths, interfaces, and exit criteria for A1-A4/U1-U4.
