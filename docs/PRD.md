# Product Requirements Document (PRD)

## Document control

| Field | Value |
|---|---|
| Product | Echo.ai - Adaptive Workflow Learning Agent |
| Version | 1.0 - shared implementation baseline |
| Owner | Advait - product/architecture coordinator |
| Status | MVP scope agreed; provider and site details may remain pending |
| Deadline | Three-hour build sprint |
| Related docs | [TRD](TRD.md) · [Runbook](RUNBOOK.md) · [Workflow](WORKFLOW.md) · [Agent rules](AGENT_RULES.md) |

## 1. Product summary

Echo.ai learns a browser workflow from a user demonstration, converts it into an understandable structured procedure, saves it only after user approval, and later resumes or executes it from a natural voice request. It inspects current page state and verifies checkpoints rather than replaying clicks blindly.

**Product promise:** Show it once. Let it learn. Ask it anytime.

Voice is the primary interaction path. The central product differentiator remains the complete learn-memory-execute lifecycle. Basic one-off voice commands and connected-app/plugin actions are future extensions after the core flow works.

## 2. Problem

People repeat multi-step digital tasks and must remember or re-explain the steps. Conventional automation often requires manual scripts or fixed rules, while a voice assistant may understand a single request without turning a demonstrated procedure into approved, reusable memory.

## 3. Target user and MVP scenario

### Target user

A person who repeatedly performs a browser-based task and wants to teach it once, then resume it later by voice.

### Demonstration scenario

The user demonstrates how to continue one Python course in one chosen website. Echo.ai observes a controlled browser session, proposes a workflow, waits for review and approval, saves it, then later opens the next appropriate item and verifies a visible progress signal.

## 4. Product principles

1. **Teach once:** A demonstration creates a candidate workflow, not a hidden recording.
2. **Voice first:** Teach, execute, pause, stop, and status requests are designed for voice; text controls provide a reliable fallback.
3. **User-approved memory:** Only reviewed and approved workflows are reusable.
4. **Verified progress:** A step advances only after a success signal is observed.
5. **Explicit permission:** Observation is scoped to the controlled browser session and stops immediately on request.
6. **Safe uncertainty:** Ask or stop when intent, target, or outcome is unclear.

## 5. Goals and acceptance criteria

| ID | Requirement | Acceptance condition |
|---|---|---|
| P0-1 | Start/stop Teach Mode | User explicitly starts observation and can stop it immediately. |
| P0-2 | Capture relevant browser events | A short task yields a normalized, task-scoped event trace. |
| P0-3 | Generate a workflow draft | Trace is expressed as ordered actions with expected results. |
| P0-4 | Review and approve | User can approve/reject before persistence; rejected drafts are not runnable. |
| P0-5 | Persist and retrieve | An approved workflow is stored and later found by a natural-language request. |
| P0-6 | Execute and verify | Agent performs a permitted next action and verifies its visible outcome. |
| P0-7 | Show status and recovery | UI/voice communicates current state and reason for pausing/failure. |
| P0-8 | Respect privacy and safety | No credential memory, arbitrary model code, blind click replay, or unconfirmed consequential action. |

## 6. Scope

### Three-hour MVP - P0

- One controlled browser and one chosen demo website/task.
- Explicit Teach Mode consent, event observation, interpretation, and structured workflow draft.
- User review and approval before saving.
- SQLite or a temporary in-memory adapter behind the same repository interface.
- Workflow retrieval, bounded planning/execution, and checkpoint verification.
- Functional React dashboard and visible fallback controls.

### P1 if available

- Real Gnani Prisma STT and Timbre TTS after a successful smoke test.
- Clear polished UI and live execution status.

### P2 / later

- WebSockets, advanced semantic memory, multiple sites, browser extension.
- Connected email, calendar, Canva, and other app/plugin actions.
- General desktop automation, multilingual dialogue, workflow sharing.

### Non-goals

- Universal autonomous agent, multiple services/databases, vector database, multi-agent runtime in the product.
- Arbitrary LLM-generated code or coordinate-only replay.
- Capturing all browsing history or storing passwords.
- Automating graded assessments or claiming course completion without evidence.

## 7. Key user journey

1. User requests Teach Mode by voice.
2. Agent explains observation and obtains explicit authorization.
3. User demonstrates a short task in the controlled browser.
4. Agent interprets the event trace and presents a structured draft.
5. User reviews and approves the workflow.
6. Later, user asks to continue the task.
7. Agent retrieves the saved workflow, inspects page state, executes an allowed action, verifies the result, updates progress, and reports it.

Detailed transitions are in [WORKFLOW.md](WORKFLOW.md).

## 8. UI requirements

- Always-visible agent state: Idle, Listening, Learning, Processing, Awaiting Approval, Executing, Waiting for Confirmation, Paused, Completed, Failed.
- Teach screen shows explicit consent and immediate stop.
- Draft review shows clear steps and expected results with approve/reject controls.
- Workflow library shows name, approval status, and last verified checkpoint.
- Execution monitor distinguishes attempted steps from verified steps and exposes pause/stop.
- Voice controls have visible text/status fallback.

## 9. Privacy and trust

- Observe only an explicitly authorized controlled session.
- Filter passwords, payment values, and unrelated sensitive fields before storage/model processing.
- Never store credentials in workflow memory; use the user's normal authenticated session.
- Require confirmation for send, purchase, delete, submission, or account-setting actions.
- Validate proposed actions against schemas and an allowlist; never execute generated code.

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Eight parallel agents create incompatible interfaces | Advait publishes contracts and owners before implementation; single-writer file rule. |
| Three-hour deadline | Make one vertical slice work; P1/P2 features stop before P0 work. |
| Gnani access is delayed | Smoke test early; use an explicitly labeled mock/text fallback. |
| Demo site changes | Pick one site, semantic locators, and visible verification signals. |
| Agents overwrite shared code | Isolated branches, explicit file ownership, change requests to owner; no direct same-file edits. |

