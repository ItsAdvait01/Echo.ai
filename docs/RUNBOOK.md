# Three-Hour Build and Integration Runbook

## 1. Purpose and finish line

Coordinate eight independent coding agents under a strict 180-minute deadline. The finish line is one integrated demonstration of **Teach -> Learn -> Approve -> Save -> Retrieve -> Execute -> Verify** on one controlled browser task. It is not eight isolated blocks or eight completed features.

## 2. Roles

- **Advait:** Chief Architect, GitHub/repository owner, API contract owner with A1, integration lead, reviewer, merge decision maker, demo readiness.
- **A1-A4:** Backend/API, persistence, Gnani voice, frontend (Advait-side four blocks).
- **U1-U4:** Browser observer, workflow learner, planner/executor, verification/recovery (Kshitij-side four blocks).
- **Kshitij:** Coordinate U1-U4, resolve their progress/blockers with Advait; do not redefine contracts independently.

## 3. Branches and GitHub protocol

```text
main
  └── integration  (shared starting commit; only Advait merges here)
        ├── agent/A1-backend-api
        ├── agent/A2-workflow-memory
        ├── agent/A3-gnani-voice
        ├── agent/A4-frontend
        ├── agent/U1-browser-observer
        ├── agent/U2-workflow-learner
        ├── agent/U3-planner-executor
        └── agent/U4-verification
```

1. `main` remains stable and demo-ready; agents never push directly to it.
2. All eight branches start from the same published `integration` commit.
3. Each agent commits and pushes only their assigned branch, with meaningful messages.
4. Agent work is proposed to `integration`; Advait reviews and merges continuously, not only in the final 30 minutes.
5. No force push, reset, or overwrite of another agent's branch/commit.
6. Contract changes require approval from Advait/A1 and a broadcast to all affected agents before implementation.
7. The branch creation is not the task handoff: each agent must confirm assignment, ownership, branch, interface, and acceptance gate before coding.

## 4. No-overwrite collaboration rules

- Read `docs/AGENT_RULES.md` and your individual `docs/agents/<ID>.md` before touching code.
- You may edit only the exclusive paths listed for your agent. Do not edit an earlier agent's code, even if it is in the same directory or the fix appears trivial.
- For a needed change in someone else's file, send an owner change request with path, reason, expected behavior, and suggested patch. The owner applies it; do not patch around or silently duplicate their implementation.
- `backend/app/schemas/contracts.py`, API routes, README, docs, and root manifests have named single owners. Consumer agents request interface changes; they never write into those files.
- U3 and U4 share a directory but own different files. Same directory does not mean shared ownership.
- Do not edit overlapping lines/sections in a shared file. Where a future shared file is unavoidable, Advait names one writer and assigns all others separate extension modules.
- Preserve another agent's work during conflict resolution. Ask the file owner and coordinator; do not delete, reformat, rename, or revert their code to make a merge easier.
- If blocked, keep the branch buildable with a narrow stub in your own file and report the blocker. Do not change somebody else's code as a shortcut.
- Every change report lists owned files modified, tests/smoke checks actually run, interface assumptions, blockers, and commit hash. Do not claim a check ran when it did not.

## 5. Three-hour schedule

| Time | Activity | Coordinator checkpoint |
|---|---|---|
| 00-20 min | Repository, architecture, contracts, docs, environment, agent prompts | Publish one contract and file ownership map; all agents acknowledge. |
| 20-100 min | Eight agents build in parallel on assigned branches | Each reports progress/blockers; integrate completed vertical slices continuously. |
| 100-140 min | Integration: API, browser, memory, voice/UI connection | Core endpoints and data flow operate together; P0 path prioritized. |
| 140-170 min | End-to-end run, bug fixing, fallback validation | One complete run plus one safe-stop/failure case. |
| 170-180 min | Freeze, README/demo preparation, final handoff | Stable demo commit on integration; no risky features. |

### Priority order

- **P0:** Observation, workflow draft, approval/persistence, retrieval, browser execution, checkpoint verification.
- **P1:** Real Gnani voice if smoke test succeeds; functional frontend and clear status.
- **P2:** WebSockets, advanced semantic memory, multiple websites. Defer these if they endanger P0.

## 6. Agent communication format

```text
AGENT: U1
STATUS: IN_PROGRESS | BLOCKED | READY_FOR_REVIEW
BRANCH: agent/U1-browser-observer
COMPLETED: ...
CURRENT: ...
BLOCKER: None | owner + requested change
FILES: only owned paths modified
CONTRACT: exact version/fields consumed
CHECKS: commands actually run and results
NEXT: ...
COMMIT: hash or pending
```

## 7. Integration order

1. Advait/A1 publishes frozen contracts and stubs.
2. Merge A2 memory interface and A1 API foundation.
3. Merge U1 event capture and U2 workflow draft pipeline.
4. Merge U3 execution and U4 verification/recovery.
5. Merge A3 voice adapter and A4 UI against the stable API.
6. Advait runs the complete app and demo; agents fix only owned paths. If an integration edit crosses ownership, Advait requests an owner patch or makes a clearly logged coordinator-only integration commit after informing the owner.

## 8. Local start and smoke path

The starter manifests define the initial commands below. If an owner changes a manifest, that owner updates the command in this section and announces it to the team.

1. Copy `.env.example` to `.env`; add keys privately only if available. Never commit it.
2. In `backend/`, create and activate a virtual environment, then run `python -m pip install -e '.[dev]'`.
3. Start the API from `backend/` with `uvicorn app.main:app --reload --port 8000`; confirm `GET http://localhost:8000/api/health` responds.
4. In `frontend/`, run `npm install`, then `npm run dev -- --host 0.0.0.0`; open the Vite URL and confirm it renders.
5. Install the Playwright Chromium browser with `python -m playwright install chromium` if the local environment has not installed it.
6. Verify Gnani adapter status. If unavailable, explicitly select mock/text fallback.
7. Open the selected demo site in the controlled Playwright session; do not observe any other browser activity.

## 9. Demo procedure

1. Say: “Learn how I continue my Python course.”
2. Confirm Teach Mode and explicitly start observation.
3. Demonstrate the agreed short browser task.
4. Stop observation and review the workflow draft.
5. Approve it and show that it appears in the workflow library.
6. Say: “Continue my Python course.”
7. Confirm workflow match and current checkpoint.
8. Allow the next bounded action; verify the visible expected state.
9. Report verified progress. If verification fails, show a safe pause and do not claim success.

## 10. Recovery and go/no-go

| Problem | Recovery |
|---|---|
| Gnani unavailable | Use labeled mock/text input; keep the same command flow. |
| Browser capture fails | Use a clearly labeled sample event trace only if the demo states it is sample data; never fabricate live observation. |
| Selector ambiguous | Pause, inspect current state, and ask; do not guess. |
| Persistence fails | Do not report saved; reconnect store or label a temporary in-memory fallback. |
| Agent needs another's file | File owner applies the requested change; keep working in own module meanwhile. |
| Integration breaks near deadline | Revert only the offending integration commit with owner/coordinator awareness; preserve branch work and return to last working integration state. |

**Go:** one end-to-end run completes with verified checkpoint, voice works or fallback is clear, and no secret is exposed.

**No-go:** observation lacks consent, execution skips validation, progress advances without verification, or the demo misrepresents mock/sample activity as real.

