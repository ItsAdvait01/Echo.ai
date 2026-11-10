# Echo.ai - Adaptive Workflow Learning Agent

**Show it once. Let it learn. Ask it anytime.**

Echo.ai is a voice-first workflow learning agent. A user demonstrates a task in an explicitly authorized, controlled browser session. Echo.ai captures relevant events, proposes a structured workflow for review, saves it after approval, and later resumes or executes it through a natural-language request. It verifies meaningful checkpoints and pauses when uncertain.

The MVP's defining path is **Teach -> Learn -> Approve -> Save -> Retrieve -> Execute -> Verify**. Live conversational voice and connected-app/plugin tasks are extensions; they do not replace the workflow-learning core.

## Start here

1. Read [`docs/PRD.md`](docs/PRD.md), [`docs/TRD.md`](docs/TRD.md), and [`docs/RUNBOOK.md`](docs/RUNBOOK.md).
2. Read [`docs/AGENT_RULES.md`](docs/AGENT_RULES.md) before editing anything.
3. Read your assigned context in [`docs/agents/`](docs/agents/).
4. Work only on your assigned branch and owned files. Do not edit another agent's code, even to make a small change in a shared file. Ask the owner to apply a change request.

## Architecture snapshot

- Frontend: React + TypeScript + Vite; Tailwind is the intended styling system.
- Backend: Python + FastAPI; Pydantic request/response contracts.
- Browser: Playwright in an explicitly controlled session.
- Memory: SQLite; SQLAlchemy planned.
- Voice: Gnani Prisma STT + Timbre TTS when access is available; text fallback is required.
- Realtime: WebSockets only if time permits; polling is an acceptable fallback.

The eight implementation agents are development workers, not production microservices. Keep one application, one database, and shared contracts. Do not add a vector database, microservices, or a multi-agent runtime to the MVP.

## Branch model

- `main`: stable, demo-ready baseline; no direct agent pushes.
- `integration`: shared implementation base; the coordinator reviews and merges agent work here.
- `agent/A1-backend-api`, `agent/A2-workflow-memory`, `agent/A3-gnani-voice`, `agent/A4-frontend`, `agent/U1-browser-observer`, `agent/U2-workflow-learner`, `agent/U3-planner-executor`, `agent/U4-verification`: isolated work branches created from the same `integration` commit.

See [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for the three-hour schedule and integration procedure.
