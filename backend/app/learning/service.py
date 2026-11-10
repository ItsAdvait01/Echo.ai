"""Service interface for the U2 workflow learner.

Exposes ``generate_workflow_draft``, ``validate_workflow_draft`` and
``submit_workflow_draft`` for internal use and for the A2 handoff. U2 generates
drafts only; it never approves or persists a workflow.

The deterministic rule-based path is always available and is used when no real
LLM provider is configured or when an LLM proposal is invalid/unavailable.
"""

from datetime import datetime, timezone
from typing import Any

from app.schemas.contracts import BrowserEvent, WorkflowDraft
from app.learning.draft_builder import DraftBuilder
from app.learning.errors import WorkflowDraftError
from app.learning.event_processor import EventProcessor
from app.learning.event_grouping import EventGrouper
from app.learning.intent_inference import IntentInference
from app.learning.llm import LLMProvider, RuleBasedFallback
from app.learning.step_extractor import StepExtractor
from app.learning.validators import DraftValidator


class WorkflowLearnerService:
    """Orchestrates the event-to-draft pipeline."""

    def __init__(self, llm_provider: LLMProvider | None = None) -> None:
        self.processor = EventProcessor()
        self.grouper = EventGrouper()
        self.inferrer = IntentInference()
        self.extractor = StepExtractor()
        self.builder = DraftBuilder()
        self.validator = DraftValidator()
        # Optional LLM; falls back to the deterministic rule-based path.
        self.llm: LLMProvider = llm_provider or RuleBasedFallback()

    def generate_workflow_draft(self, session_id: str, events: list[BrowserEvent]) -> WorkflowDraft:
        """Run the full pipeline and return a review-ready ``WorkflowDraft``.

        Never approves or persists the draft. Raises :class:`WorkflowDraftError`
        with structured details when the input cannot be safely learned from.
        """
        normalized = self.processor.process(events)
        groups = self.grouper.group(normalized.events)

        if not groups:
            raise WorkflowDraftError(
                "insufficient_evidence",
                (
                    "The demonstration contained no groupable, meaningful "
                    "events; cannot infer a workflow."
                ),
                {
                    "session_id": session_id,
                    "event_count": len(normalized.events),
                    "warnings": normalized.warnings,
                },
            )

        proposal = self._propose(session_id, normalized.events, groups)
        draft = self._draft_from_proposal(session_id, proposal, groups)
        self.validator.assert_reviewable(draft)
        return draft

    def validate_workflow_draft(self, draft: WorkflowDraft) -> dict[str, Any]:
        """Validate a draft without approving it. Returns a structured result."""
        result = self.validator.validate(draft)
        return {
            "valid": result.valid,
            "errors": result.errors,
            "status": "draft",
            "step_count": len(draft.steps),
            "never_auto_approved": True,
        }

    def submit_workflow_draft(
        self, draft: WorkflowDraft, session_id: str | None = None
    ) -> dict[str, Any]:
        """Prepare a draft for handoff to the review/approval layer (A1/A4).

        Returns a structured handoff payload. The draft is NOT approved or
        persisted here; approval is a user decision applied at the A1/A2
        approval boundary.
        """
        self.validator.assert_reviewable(draft)
        workflow_id = self._stable_workflow_id(draft)
        created_at = datetime.now(timezone.utc)
        workflow = self.builder.draft_to_workflow_placeholder(draft, workflow_id, created_at)
        return {
            "workflow_id": workflow_id,
            "status": "draft",
            "approved": False,
            "created_at": created_at.isoformat(),
            "session_id": session_id,
            "name": workflow.name,
            "description": workflow.description,
            "version": 1,
            "trigger_phrases": list(workflow.trigger_phrases),
            "steps": [s.model_dump() for s in workflow.steps],
            "uncertainty_notes": list(workflow.uncertainty_notes),
            "draft": draft,
            "workflow": workflow,
            "handoff_ready": True,
            "requires_user_approval": True,
        }

    # ------------------------------------------------------------------ #
    # Internal pipeline helpers
    # ------------------------------------------------------------------ #
    def _propose(
        self,
        session_id: str,
        events: list[BrowserEvent],
        groups: list[Any],
    ) -> dict[str, Any]:
        """Produce a workflow proposal via LLM or the rule-based fallback.

        A real (non-fallback) LLM provider is tried first; any failure or
        invalid response falls back to the deterministic rules.
        """
        if not isinstance(self.llm, RuleBasedFallback):
            try:
                proposal = self.llm.generate_workflow_proposal(session_id=session_id, events=events)
                if self._is_usable_proposal(proposal):
                    return proposal
            except Exception:
                # LLM unavailable or raised; fall back to rules.
                pass

        fallback = RuleBasedFallback()
        return fallback.generate_workflow_proposal(session_id=session_id, events=events)

    @staticmethod
    def _is_usable_proposal(proposal: Any) -> bool:
        if not isinstance(proposal, dict):
            return False
        return bool(proposal.get("steps")) and isinstance(proposal.get("steps"), list)

    def _draft_from_proposal(
        self,
        session_id: str,
        proposal: dict[str, Any],
        groups: list[Any],
    ) -> WorkflowDraft:
        inference = proposal.get("inference", {})
        name = self._workflow_name(inference)
        description = self._workflow_description(session_id, inference)
        uncertainty_notes: list[str] = []
        for note in proposal.get("uncertainty_notes", []):
            if note and note not in uncertainty_notes:
                uncertainty_notes.append(note)
        if inference.get("ambiguity") and inference["ambiguity"] not in uncertainty_notes:
            uncertainty_notes.append(inference["ambiguity"])
        steps = proposal.get("steps", [])
        return self.builder.build(
            session_id=session_id,
            name=name,
            description=description,
            steps=steps,
            inference=inference,
            uncertainty_notes=uncertainty_notes,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _workflow_name(inference: dict[str, Any]) -> str:
        intent = inference.get("intent", "demonstrated task")
        if intent and intent != "unclear":
            lowered = intent.lower()
            if "course" in lowered or "python" in lowered:
                return "Continue my Python course"
            if "next lesson" in lowered:
                return "Continue to the next lesson"
            return f"Continue {intent.split(';')[0].strip()}"
        return "Continue the demonstrated task"

    @staticmethod
    def _workflow_description(session_id: str, inference: dict[str, Any]) -> str:
        intent = inference.get("intent", "demonstrated task")
        return f"Learned from session {session_id}: {intent}".strip()

    @staticmethod
    def _stable_workflow_id(draft: WorkflowDraft) -> str:
        import uuid

        return str(uuid.uuid4())

    def llm_proposal_available(self) -> bool:
        """Whether a non-fallback LLM provider is configured."""
        return not isinstance(self.llm, RuleBasedFallback)
