# Product Workflow and State Specification

## 1. Teach Mode

1. User requests teaching by voice/text.
2. Agent explains scope and asks for explicit authorization.
3. A controlled Playwright session starts; UI shows Learning and Stop/Pause.
4. Observer emits normalized, filtered browser events. Password/payment values are not captured.
5. User stops observation; event stream is sealed.
6. Learner distinguishes observed facts from inferred intent and proposes workflow steps.
7. User reviews, edits if supported, and approves or rejects. Only approved workflows are stored as runnable.

## 2. Execute Mode

1. User requests a workflow by natural language.
2. Router finds exactly one approved workflow; zero/multiple matches prompt clarification.
3. Planner reads current browser state and last verified checkpoint.
4. Executor performs an allowlisted action with a validated target.
5. Verifier checks expected result; only verified steps advance persistent progress.
6. Agent reports result through voice or visible text. Uncertainty pauses the run.

## 3. State model

`idle -> listening -> processing -> awaiting_consent -> learning -> workflow_draft -> awaiting_approval -> approved/saved -> executing -> verifying -> completed`

Alternate transitions: `learning -> paused/stopped`; `awaiting_approval -> rejected`; `executing/verifying -> waiting_for_confirmation/paused/failed`; failed verification returns to the last verified checkpoint and does not advance progress.

## 4. Safe action rules

- No observation without explicit consent.
- No action outside the controlled task/session.
- No arbitrary model-generated Python or JavaScript.
- No blind coordinate replay; use semantic targets and verify them.
- Ask before send, purchase, delete, final submit, or account/security changes.
- Do not automate graded tests or misrepresent course completion.
- Stop when the page differs materially, target is ambiguous, or expected result cannot be verified.

## 5. Acceptance scenario

User teaches a short Python course navigation path; agent captures it, drafts it, user approves, workflow persists, then a later voice request retrieves it, inspects state, executes the next permitted action, verifies the visible checkpoint, and reports the result. A failed check must visibly pause without advancing.
