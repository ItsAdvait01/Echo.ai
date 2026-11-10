"""Workflow draft assembly.

Builds an A1-owned ``WorkflowDraft`` from extracted steps and the inferred
intent. The draft is always left unreviewed/unapproved: U2 never approves or
persists a workflow.
"""

from typing import Any

from app.schemas.contracts import Workflow, WorkflowDraft, WorkflowStep

STATUS_DRAFT = "draft"


class DraftBuilder:
    """Assembles a review-ready ``WorkflowDraft``."""

    def build(
        self,
        *,
        session_id: str,
        name: str,
        description: str,
        steps: list[dict[str, Any]],
        inference: dict[str, Any],
        uncertainty_notes: list[str] | None = None,
    ) -> WorkflowDraft:
        pydantic_steps = [
            WorkflowStep(
                id=s["id"],
                order=int(s["order"]),
                action_type=str(s["action_type"]),
                target=dict(s.get("target", {})),
                expected_result=dict(s.get("expected_result", {})),
                risk=str(s.get("risk", "low")),
            )
            for s in steps
        ]

        # Validate step orders are sequential and start at 1.
        for index, step in enumerate(pydantic_steps, start=1):
            if step.order != index:
                step.order = index

        return WorkflowDraft(
            name=name or "Unnamed workflow",
            description=description or "",
            trigger_phrases=list(inference.get("trigger_phrases", [])),
            steps=pydantic_steps,
            uncertainty_notes=list(uncertainty_notes or []),
        )

    def to_summary(self, draft: WorkflowDraft) -> dict[str, Any]:
        """Return a review-friendly, serialization-safe summary for A4."""
        return {
            "name": draft.name,
            "description": draft.description,
            "status": STATUS_DRAFT,
            "trigger_phrases": list(draft.trigger_phrases),
            "step_count": len(draft.steps),
            "steps": [
                {
                    "step_id": s.id,
                    "order": s.order,
                    "description": s.action_type,
                    "target": s.target,
                    "expected_result": s.expected_result,
                    "confidence": s.target.get("confidence"),
                    "inference_status": s.target.get("inference_status"),
                    "requires_confirmation": s.target.get("requires_confirmation", False),
                    "evidence_event_ids": s.target.get("evidence_event_ids", []),
                    "risk": s.risk,
                }
                for s in draft.steps
            ],
            "uncertainty_notes": list(draft.uncertainty_notes),
            "never_auto_approved": True,
        }

    def draft_to_workflow_placeholder(
        self, draft: WorkflowDraft, workflow_id: str, created_at
    ) -> Workflow:
        """Build a ``Workflow`` (A2 memory model) without marking it approved.

        U2 may hand this to A2 for review-only storage; approval remains a user
        decision and is applied by A1/A2 at the approval boundary.
        """
        return Workflow(
            id=workflow_id,
            name=draft.name,
            description=draft.description,
            trigger_phrases=list(draft.trigger_phrases),
            steps=list(draft.steps),
            uncertainty_notes=list(draft.uncertainty_notes),
            approved=False,
            created_at=created_at,
        )
