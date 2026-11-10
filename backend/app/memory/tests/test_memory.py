"""Tests for the workflow memory boundary. Owned by A2.

Run from ``backend/`` with ``python -m pytest app/memory/tests -q``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.memory.errors import (
    MemoryValidationError,
    UnapprovedWorkflowError,
    UnverifiedProgressError,
    WorkflowNotFoundError,
)
from app.memory.repository import InMemoryWorkflowRepository
from app.memory.sqlite_repository import SQLiteWorkflowRepository
from app.schemas.contracts import AgentState, Workflow, WorkflowStep


def make_workflow(*, approved: bool = False, workflow_id: str = "wf-python") -> Workflow:
    return Workflow(
        id=workflow_id,
        name="Continue Python course",
        description="Open the next lesson of the Python course.",
        trigger_phrases=["continue my python course"],
        steps=[
            WorkflowStep(
                id="step-open",
                order=1,
                action_type="navigate",
                target={"url": "https://example.test/course"},
                expected_result={"text_visible": "Python Basics"},
            ),
            WorkflowStep(
                id="step-next",
                order=2,
                action_type="click",
                target={"role": "link", "name": "Next lesson"},
                expected_result={"text_visible": "Lesson 2"},
            ),
        ],
        uncertainty_notes=["Lesson numbering assumed."],
        approved=approved,
        created_at=datetime.now(UTC),
    )


def evidence(step: int) -> dict[str, object]:
    return {"verified": True, "step_order": step, "observed": f"lesson-{step}"}


@pytest.fixture(params=["sqlite", "memory"])
def repo(request: pytest.FixtureRequest, tmp_path):
    if request.param == "sqlite":
        return SQLiteWorkflowRepository(tmp_path / "echo-memory.db")
    return InMemoryWorkflowRepository()


# --- acceptance: approved workflows persist and are retrievable ----------


def test_approved_workflow_can_be_saved_and_retrieved(repo) -> None:
    repo.save_approved(make_workflow(approved=True))

    stored = repo.get("wf-python")
    assert stored is not None
    assert stored.approved is True
    assert stored.name == "Continue Python course"
    assert [step.order for step in stored.steps] == [1, 2]
    assert stored.steps[1].expected_result == {"text_visible": "Lesson 2"}
    assert [workflow.id for workflow in repo.list_approved()] == ["wf-python"]


def test_draft_approval_flow_makes_workflow_runnable(repo) -> None:
    repo.save_draft(make_workflow())
    assert repo.get("wf-python") is None
    assert repo.list_approved() == []
    assert repo.get_draft("wf-python") is not None

    approved = repo.approve("wf-python")
    assert approved.approved is True
    assert repo.get("wf-python") is not None
    assert [workflow.id for workflow in repo.list_approved()] == ["wf-python"]


def test_draft_is_not_returned_as_executable_memory(repo) -> None:
    repo.save_draft(make_workflow())

    assert repo.get("wf-python") is None
    assert repo.list_approved() == []
    with pytest.raises(UnapprovedWorkflowError):
        repo.create_run("wf-python")
    assert repo.list_runs() == []


def test_rejected_draft_is_not_returned_as_executable_memory(repo) -> None:
    repo.save_draft(make_workflow())
    repo.approve("wf-python")
    rejected = repo.reject("wf-python")

    assert rejected.approved is False
    assert repo.get("wf-python") is None
    assert repo.list_approved() == []
    with pytest.raises(UnapprovedWorkflowError):
        repo.create_run("wf-python")


def test_saving_unapproved_workflow_as_reusable_is_refused(repo) -> None:
    with pytest.raises(UnapprovedWorkflowError):
        repo.save_approved(make_workflow(approved=False))


def test_save_draft_refuses_already_approved_workflow(repo) -> None:
    with pytest.raises(UnapprovedWorkflowError):
        repo.save_draft(make_workflow(approved=True))


def test_missing_workflow_is_reported(repo) -> None:
    assert repo.get("nope") is None
    with pytest.raises(WorkflowNotFoundError):
        repo.approve("nope")
    with pytest.raises(WorkflowNotFoundError):
        repo.create_run("nope")


def test_workflow_without_steps_cannot_be_approved(repo) -> None:
    empty = make_workflow().model_copy(update={"steps": []})
    repo.save_draft(empty)

    with pytest.raises(MemoryValidationError):
        repo.approve("wf-python")
    assert repo.get("wf-python") is None


def test_duplicate_step_orders_cannot_be_approved(repo) -> None:
    workflow = make_workflow()
    workflow.steps.append(workflow.steps[0].model_copy(update={"id": "dup"}))
    repo.save_draft(workflow)

    with pytest.raises(MemoryValidationError):
        repo.approve("wf-python")
    assert repo.get("wf-python") is None


# --- progress requires verified results ----------------------------------


def test_progress_requires_verified_flag(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    run = repo.create_run("wf-python")

    with pytest.raises(UnverifiedProgressError):
        repo.record_verified_progress(
            run.id, step_order=1, verified=False, verification=evidence(1)
        )
    progress = repo.get_progress(run.id)
    assert progress.run.last_verified_step == 0
    assert progress.verified_checkpoints == []


def test_progress_requires_verifier_evidence(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    run = repo.create_run("wf-python")

    with pytest.raises(UnverifiedProgressError):
        repo.record_verified_progress(run.id, step_order=1, verified=True)
    assert repo.get_progress(run.id).verified_checkpoints == []


def test_failed_verification_does_not_advance(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    run = repo.create_run("wf-python")

    with pytest.raises(UnverifiedProgressError):
        repo.record_verified_progress(
            run.id,
            step_order=1,
            verified=True,
            verification={"verified": False, "reason": "target not visible"},
        )
    assert repo.get_progress(run.id).run.last_verified_step == 0


def test_verified_progress_advances_and_is_persisted(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    run = repo.create_run("wf-python")

    progress = repo.record_verified_progress(
        run.id, step_order=1, verified=True, verification=evidence(1)
    )
    assert progress.run.last_verified_step == 1
    assert [checkpoint.step_order for checkpoint in progress.verified_checkpoints] == [1]
    assert progress.verified_checkpoints[0].verification["observed"] == "lesson-1"

    reloaded = repo.get_progress(run.id)
    assert reloaded.run.last_verified_step == 1
    assert len(reloaded.verified_checkpoints) == 1


def test_repeated_checkpoint_is_idempotent(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    run = repo.create_run("wf-python")

    first = repo.record_verified_progress(
        run.id, step_order=1, verified=True, verification=evidence(1)
    )
    second = repo.record_verified_progress(
        run.id, step_order=1, verified=True, verification=evidence(1)
    )

    assert len(second.verified_checkpoints) == 1
    assert second.verified_checkpoints[0].verified_at == first.verified_checkpoints[0].verified_at
    assert second.run.last_verified_step == 1


def test_late_checkpoint_does_not_regress_progress(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    run = repo.create_run("wf-python")

    repo.record_verified_progress(run.id, step_order=2, verified=True, verification=evidence(2))
    progress = repo.record_verified_progress(
        run.id, step_order=1, verified=True, verification=evidence(1)
    )

    assert progress.run.last_verified_step == 2
    assert [checkpoint.step_order for checkpoint in progress.verified_checkpoints] == [1, 2]


def test_unknown_step_order_is_rejected(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    run = repo.create_run("wf-python")

    with pytest.raises(MemoryValidationError):
        repo.record_verified_progress(
            run.id, step_order=99, verified=True, verification=evidence(99)
        )
    assert repo.get_progress(run.id).run.last_verified_step == 0


def test_latest_progress_tracks_workflow(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    assert repo.get_latest_progress("wf-python") is None

    run = repo.create_run("wf-python")
    repo.record_verified_progress(run.id, step_order=1, verified=True, verification=evidence(1))

    latest = repo.get_latest_progress("wf-python")
    assert latest is not None
    assert latest.run.id == run.id
    assert latest.run.last_verified_step == 1


# --- run lifecycle -------------------------------------------------------


def test_run_state_is_recorded_and_terminal_state_stamps_end(repo) -> None:
    repo.save_approved(make_workflow(approved=True))
    run = repo.create_run("wf-python")
    assert run.state is AgentState.EXECUTING
    assert run.started_at is not None
    assert run.ended_at is None

    paused = repo.set_run_state(run.id, AgentState.PAUSED)
    assert paused.state is AgentState.PAUSED
    assert paused.ended_at is None

    completed = repo.set_run_state(run.id, AgentState.COMPLETED)
    assert completed.state is AgentState.COMPLETED
    assert completed.ended_at is not None
    assert repo.get_run(run.id).state is AgentState.COMPLETED


# --- storage characteristics --------------------------------------------


def test_sqlite_data_survives_a_new_repository_instance(tmp_path) -> None:
    db_path = tmp_path / "durable.db"
    first = SQLiteWorkflowRepository(db_path)
    assert first.is_durable is True
    first.save_approved(make_workflow(approved=True))
    run = first.create_run("wf-python")
    first.record_verified_progress(
        run.id, step_order=1, verified=True, verification=evidence(1)
    )
    first.close()

    second = SQLiteWorkflowRepository(db_path)
    restored = second.get("wf-python")
    assert restored is not None and restored.approved is True
    progress = second.get_progress(run.id)
    assert progress.run.last_verified_step == 1
    assert progress.verified_checkpoints[0].workflow_id == "wf-python"


def test_in_memory_adapter_is_explicitly_not_durable() -> None:
    repo = InMemoryWorkflowRepository()
    assert repo.is_durable is False
