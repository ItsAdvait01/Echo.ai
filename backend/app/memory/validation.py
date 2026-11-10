"""Input guards shared by both memory adapters. Owned by A2."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.memory.errors import (
    MemoryValidationError,
    UnapprovedWorkflowError,
    UnverifiedProgressError,
)
from app.schemas.contracts import Workflow


def validate_runnable(workflow: Workflow) -> None:
    """Guard the executable-memory boundary: approved, with unambiguous steps.

    Drafts are stored as-is so the review screen can show incomplete or
    uncertain proposals; this check runs when a draft becomes runnable.
    """
    if not workflow.approved:
        raise UnapprovedWorkflowError(
            workflow.id,
            "Call approve() first, or save the draft with save_draft().",
        )
    if not workflow.steps:
        raise MemoryValidationError(
            f"Workflow {workflow.id!r} has no steps and cannot be saved as reusable memory."
        )
    orders = [step.order for step in workflow.steps]
    if len(set(orders)) != len(orders):
        raise MemoryValidationError(
            f"Workflow {workflow.id!r} has duplicate step orders; progress would be ambiguous."
        )


def require_verification(
    *,
    step_order: int,
    verified: bool,
    verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return normalized verifier evidence or refuse to advance progress.

    Progress may only move on the caller's explicit ``verified=True`` plus the
    verifier's own result. An attempted-but-unverified step is never complete.
    """
    if step_order < 1:
        raise MemoryValidationError(f"step_order must be >= 1, got {step_order}.")
    if not verified:
        raise UnverifiedProgressError(
            f"Step {step_order} has no verified result; attempted steps are not progress."
        )
    if not verification:
        raise UnverifiedProgressError(
            f"Step {step_order} requires the verifier's result as evidence."
        )

    evidence = dict(verification)
    if evidence.get("verified") is False or evidence.get("passed") is False:
        raise UnverifiedProgressError(
            f"Step {step_order} failed verification and cannot be marked complete."
        )
    try:
        return json.loads(json.dumps(evidence, default=str))
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise MemoryValidationError(
            f"Verification result for step {step_order} is not JSON-serializable."
        ) from exc
