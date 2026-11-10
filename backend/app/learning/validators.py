"""Draft validation.

Enforces that a produced ``WorkflowDraft`` is structurally sound and remains in
draft/review state (never approved) before it is handed to the approval layer.
"""

from app.schemas.contracts import WorkflowDraft


class DraftValidationResult:
    def __init__(self, valid: bool, errors: list[str] | None = None) -> None:
        self.valid = valid
        self.errors = errors or []


class DraftValidator:
    def validate(self, draft: WorkflowDraft) -> DraftValidationResult:
        errors: list[str] = []

        if not draft or not isinstance(draft, WorkflowDraft):
            return DraftValidationResult(False, ["draft is not a WorkflowDraft"])

        if not draft.name or not draft.name.strip():
            errors.append("workflow name is required")

        if not draft.steps:
            errors.append("workflow must contain at least one step")

        seen_orders: set[int] = set()
        for step in draft.steps:
            if step.order < 1:
                errors.append(f"step {step.id} has invalid order {step.order}")
            if step.order in seen_orders:
                errors.append(f"duplicate step order {step.order}")
            seen_orders.add(step.order)
            if not step.action_type or not step.action_type.strip():
                errors.append(f"step {step.id} is missing an action_type")
            # Every step must be traceable to evidence (MVP requirement).
            evidence = step.target.get("evidence_event_ids", [])
            if not evidence:
                errors.append(f"step {step.id} has no supporting evidence event ids")

        expected = list(range(1, len(draft.steps) + 1))
        actual = sorted(s.order for s in draft.steps)
        if actual != expected:
            errors.append("step orders must be sequential starting at 1")

        # U2 never issues an approved workflow: drafts are unreviewed by default.
        # The Workflow model carries the approved flag; WorkflowDraft has none.
        return DraftValidationResult(valid=len(errors) == 0, errors=errors)

    def assert_reviewable(self, draft: WorkflowDraft) -> None:
        result = self.validate(draft)
        if not result.valid:
            from app.learning.errors import WorkflowDraftError

            raise WorkflowDraftError(
                "invalid_draft",
                "Workflow draft failed validation and is not reviewable.",
                {"errors": result.errors},
            )
