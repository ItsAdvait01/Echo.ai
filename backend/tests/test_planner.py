"""Unit tests for U3 workflow planner. Deterministic; no browser, no network."""

from datetime import datetime, timezone

from app.execution.planner import WorkflowPlanner
from app.schemas.contracts import Workflow, WorkflowStep


def _workflow(approved: bool, orders: list[int]) -> Workflow:
    steps = [
        WorkflowStep(
            id=f"step_{order}",
            order=order,
            action_type="click_semantic_target",
            target={"role": "link", "name": f"item {order}"},
            expected_result={"type": "navigation"},
        )
        for order in orders
    ]
    return Workflow(
        id="wf_python_course",
        name="Python course",
        description="Continue the Python course",
        trigger_phrases=["continue my python course"],
        steps=steps,
        approved=approved,
        created_at=datetime.now(timezone.utc),
    )


def test_next_step_returns_immediate_successor():
    planner = WorkflowPlanner()
    workflow = _workflow(approved=True, orders=[1, 2, 3])
    step = planner.next_step(workflow, last_verified_order=0)
    assert step is not None
    assert step.order == 1
    assert step.id == "step_1"


def test_next_step_respects_verified_checkpoint():
    planner = WorkflowPlanner()
    workflow = _workflow(approved=True, orders=[1, 2, 3])
    step = planner.next_step(workflow, last_verified_order=1)
    assert step is not None
    assert step.order == 2


def test_next_step_none_when_workflow_complete():
    planner = WorkflowPlanner()
    workflow = _workflow(approved=True, orders=[1, 2, 3])
    assert planner.next_step(workflow, last_verified_order=3) is None


def test_next_step_none_for_unapproved_workflow():
    planner = WorkflowPlanner()
    workflow = _workflow(approved=False, orders=[1, 2, 3])
    assert planner.next_step(workflow, last_verified_order=0) is None


def test_next_step_none_for_empty_workflow():
    planner = WorkflowPlanner()
    workflow = _workflow(approved=True, orders=[])
    assert planner.next_step(workflow, last_verified_order=0) is None


def test_plan_structure():
    planner = WorkflowPlanner()
    workflow = _workflow(approved=True, orders=[1, 2])
    plan = planner.plan(workflow, last_verified_order=0)
    assert plan["workflow_id"] == "wf_python_course"
    assert plan["approved"] is True
    assert plan["last_verified_order"] == 0
    assert plan["status"] == "planned"
    assert plan["next_step"] is not None
    assert plan["next_step"].order == 1


def test_plan_completed_when_no_next_step():
    planner = WorkflowPlanner()
    workflow = _workflow(approved=True, orders=[1])
    plan = planner.plan(workflow, last_verified_order=1)
    assert plan["status"] == "completed"
    assert plan["next_step"] is None
