"""Unit and integration tests for all A1 backend routes. Owned by A1."""

from starlette.testclient import TestClient

from app.api.routes.workflows import stage_draft
from app.main import app
from app.schemas.contracts import AgentState, VoiceIntent, WorkflowStep

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "echo-api"
    assert data["version"] == "0.1.0"


def test_teach_session_lifecycle():
    # 1. Require consent
    no_consent = client.post(
        "/api/learning/start",
        json={"target_url": "https://example.com/course", "consent_confirmed": False},
    )
    assert no_consent.status_code == 400
    assert no_consent.json()["code"] == "consent_required"

    # 2. Start session — a real controlled browser at a local page (no network)
    start_res = client.post(
        "/api/learning/start",
        json={"target_url": "about:blank", "consent_confirmed": True},
    )
    assert start_res.status_code == 200
    session_id = start_res.json()["session_id"]
    assert start_res.json()["state"] == AgentState.LEARNING

    # 3. Events retrieval
    events_res = client.get(f"/api/learning/{session_id}/events")
    assert events_res.status_code == 200
    assert "events" in events_res.json()

    # 4. Stop session
    stop_res = client.post(
        f"/api/learning/{session_id}/stop",
        json={"generate_draft": True},
    )
    assert stop_res.status_code == 200
    stopped = stop_res.json()
    # An empty demonstration cannot always be learned from; both outcomes must
    # be reported honestly, and any produced draft must be staged for review.
    assert stopped["state"] in (AgentState.PROCESSING, AgentState.AWAITING_APPROVAL)
    if stopped["state"] == AgentState.AWAITING_APPROVAL:
        assert stopped["draft_id"]
        assert stopped["draft"]["steps"]
    else:
        assert stopped["draft"] is None
        assert stopped["draft_id"] is None

    # 5. Double stop should fail with 409
    double_stop = client.post(f"/api/learning/{session_id}/stop")
    assert double_stop.status_code == 409
    assert double_stop.json()["code"] == "invalid_state"

    # 6. Non-existent session
    missing_res = client.get("/api/learning/nonexistent-id/events")
    assert missing_res.status_code == 404
    assert missing_res.json()["code"] == "not_found"


def test_workflow_lifecycle():
    # 1. Initially empty
    list_res = client.get("/api/workflows")
    assert list_res.status_code == 200

    # 2. Stage draft
    steps = [
        WorkflowStep(
            id="step-1",
            order=1,
            action_type="navigate_to_known_page",
            target={"url": "https://course.example.com"},
            expected_result={"title_contains": "Python Course"},
        ),
        WorkflowStep(
            id="step-2",
            order=2,
            action_type="click_semantic_target",
            target={"selector": "button.next"},
            expected_result={"visible": "Lesson 2"},
        ),
    ]
    draft = stage_draft(
        name="Python Next Lesson",
        description="Demo workflow",
        trigger_phrases=["continue python", "next lesson"],
        steps=steps,
        uncertainty_notes=[],
    )

    # 3. Read draft
    draft_res = client.get(f"/api/workflows/{draft.id}")
    assert draft_res.status_code == 200
    assert draft_res.json()["workflow"]["approved"] is False

    # 4. Reject workflow draft
    reject_draft = stage_draft("To Reject", "", [], [], [])
    reject_res = client.post(
        f"/api/workflows/{reject_draft.id}/approve",
        json={"approved": False},
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["workflow"]["approved"] is False

    # Rejected draft is discarded
    gone_res = client.get(f"/api/workflows/{reject_draft.id}")
    assert gone_res.status_code == 404

    # 5. Approve draft with name override
    approve_res = client.post(
        f"/api/workflows/{draft.id}/approve",
        json={"approved": True, "name_override": "Python Approved Course"},
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["workflow"]["approved"] is True
    assert approve_res.json()["workflow"]["name"] == "Python Approved Course"

    # 6. Now visible in list
    new_list = client.get("/api/workflows")
    assert any(w["id"] == draft.id for w in new_list.json()["workflows"])


def test_execution_lifecycle():
    # 1. Execution with nonexistent workflow
    missing_exec = client.post(
        "/api/executions",
        json={"workflow_id": "nonexistent-wf"},
    )
    assert missing_exec.status_code == 404
    assert missing_exec.json()["code"] == "not_found"

    # 2. Execution with valid approved workflow
    steps = [
        WorkflowStep(
            id="s1",
            order=1,
            action_type="navigate_to_known_page",
            target={"url": "https://example.com"},
            expected_result={},
        )
    ]
    wf = stage_draft("Exec Test", "", [], steps, [])
    client.post(f"/api/workflows/{wf.id}/approve", json={"approved": True})

    exec_res = client.post("/api/executions", json={"workflow_id": wf.id})
    assert exec_res.status_code == 200
    run_id = exec_res.json()["run"]["id"]

    # 3. Check run status
    status_res = client.get(f"/api/executions/{run_id}")
    assert status_res.status_code == 200
    assert status_res.json()["run"]["id"] == run_id

    # 4. Pause execution
    pause_res = client.post(f"/api/executions/{run_id}/pause")
    assert pause_res.status_code == 200
    assert pause_res.json()["run"]["state"] == AgentState.PAUSED

    # 5. Stop execution
    stop_res = client.post(f"/api/executions/{run_id}/stop")
    assert stop_res.status_code == 200
    assert stop_res.json()["run"]["state"] == AgentState.COMPLETED

    # 6. Double stop execution is terminal error
    double_stop = client.post(f"/api/executions/{run_id}/stop")
    assert double_stop.status_code == 409


def test_voice_command_intake():
    test_cases = [
        ("Start teaching", VoiceIntent.TEACH_START),
        ("Learn how I continue my Python course", VoiceIntent.TEACH_START),
        ("Stop teaching", VoiceIntent.TEACH_STOP),
        ("Stop observation", VoiceIntent.TEACH_STOP),
        ("Continue my Python course", VoiceIntent.WORKFLOW_EXECUTE),
        ("Run the workflow", VoiceIntent.WORKFLOW_EXECUTE),
        ("Pause", VoiceIntent.EXECUTION_PAUSE),
        ("Stop", VoiceIntent.EXECUTION_STOP),
        ("Status", VoiceIntent.STATUS_GET),
        ("gibberish random utterance", None),
    ]

    for transcript, expected in test_cases:
        res = client.post(
            "/api/voice/command",
            json={"transcript": transcript, "source": "voice"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["intent"] == expected
        assert data["transcript"] == transcript
        assert "reply_text" in data
