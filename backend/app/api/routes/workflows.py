"""
Workflow routes — listing, retrieval, and approval.  Owned by A1.

Delegates persistence to A2's WorkflowRepository.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.core.dependencies import get_workflow_repository
from app.core.errors import InvalidStateError, NotFoundError
from app.memory.repository import WorkflowRepository
from app.schemas.contracts import (
    Workflow,
    WorkflowApproveRequest,
    WorkflowListResponse,
    WorkflowResponse,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])

# ---------------------------------------------------------------------------
# In-process draft staging area — only A1 writes here.
# Once approved, the draft is passed to A2's repository for persistence.
# ---------------------------------------------------------------------------
_drafts: dict[str, Workflow] = {}


def stage_draft(name: str, description: str, trigger_phrases: list[str],
                steps: list, uncertainty_notes: list[str]) -> Workflow:
    """Create a Workflow object in draft (unapproved) state.

    Called internally by A1 routes (e.g. after U2 generates a draft).
    """
    workflow = Workflow(
        id=uuid.uuid4().hex[:12],
        name=name,
        description=description,
        trigger_phrases=trigger_phrases,
        steps=steps,
        uncertainty_notes=uncertainty_notes,
        approved=False,
        created_at=datetime.now(timezone.utc),
    )
    _drafts[workflow.id] = workflow
    return workflow


@router.get("", response_model=WorkflowListResponse)
def list_workflows(
    repo: WorkflowRepository = Depends(get_workflow_repository),  # type: ignore[type-arg]
) -> WorkflowListResponse:
    """List all approved (runnable) workflows."""
    workflows = repo.list_approved()
    return WorkflowListResponse(workflows=workflows, count=len(workflows))


@router.get("/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(
    workflow_id: str,
    repo: WorkflowRepository = Depends(get_workflow_repository),  # type: ignore[type-arg]
) -> WorkflowResponse:
    """Retrieve a single workflow by ID (approved or draft)."""
    # Check persistent store first, then drafts
    workflow = repo.get(workflow_id)
    if workflow is None:
        workflow = _drafts.get(workflow_id)
    if workflow is None:
        raise NotFoundError("Workflow", workflow_id)
    return WorkflowResponse(workflow=workflow)


@router.post("/{workflow_id}/approve", response_model=WorkflowResponse)
def approve_workflow(
    workflow_id: str,
    body: WorkflowApproveRequest,
    repo: WorkflowRepository = Depends(get_workflow_repository),  # type: ignore[type-arg]
) -> WorkflowResponse:
    """Approve or reject a workflow draft.

    Only approved workflows are persisted as runnable memory.
    Rejected drafts are discarded and never become runnable.
    """
    workflow = _drafts.get(workflow_id)
    if workflow is None:
        raise NotFoundError("Draft workflow", workflow_id)

    if workflow.approved:
        raise InvalidStateError(f"Workflow '{workflow_id}' is already approved.")

    if not body.approved:
        # Reject — remove from drafts, do not persist
        del _drafts[workflow_id]
        rejected = workflow.model_copy(update={"approved": False})
        return WorkflowResponse(
            workflow=rejected,
            message="Workflow rejected and discarded.",
        )

    # Approve — apply optional rename and persist via A2
    updates: dict = {"approved": True}
    if body.name_override:
        updates["name"] = body.name_override

    approved_wf = workflow.model_copy(update=updates)
    saved = repo.save_approved(approved_wf)

    # Remove from staging
    _drafts.pop(workflow_id, None)

    return WorkflowResponse(
        workflow=saved,
        message="Workflow approved and saved.",
    )
