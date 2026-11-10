"""LLM integration point with a mandatory rule-based fallback.

U2 does not assume API keys or a provider. The deterministic rule-based
fallback always works; an LLM can be plugged in later behind the same
``LLMProvider`` interface. The LLM only ever receives sanitized event summaries
and returns a structured proposal that is validated by Pydantic before use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.schemas.contracts import BrowserEvent
from app.learning.event_processor import EventProcessor
from app.learning.event_grouping import EventGrouper
from app.learning.intent_inference import IntentInference
from app.learning.step_extractor import StepExtractor


class LLMProvider(ABC):
    """Optional structured proposal source. Not required for the MVP."""

    @abstractmethod
    def generate_workflow_proposal(
        self, *, session_id: str, events: list[BrowserEvent]
    ) -> dict[str, Any]:
        """Return a raw workflow proposal dict, or raise if unavailable."""


class RuleBasedFallback(LLMProvider):
    """Deterministic rule-based workflow proposal.

    Supports: navigation followed by a page transition, opening a course or
    content item, selecting a lesson, moving to another lesson, and recognizing
    a visible completion indicator. Explicitly not a general-purpose model.
    """

    def generate_workflow_proposal(
        self, *, session_id: str, events: list[BrowserEvent]
    ) -> dict[str, Any]:
        processor = EventProcessor()
        normalized = processor.process(events)
        grouper = EventGrouper()
        groups = grouper.group(normalized.events)
        inferrer = IntentInference()
        inference = inferrer.infer_workflow(groups)
        extractor = StepExtractor()
        steps = extractor.extract(groups, inference)

        # Build review-friendly uncertainty notes.
        uncertainty_notes: list[str] = []
        for step in steps:
            target = step.get("target", {})
            if target.get("requires_confirmation"):
                ambiguity = target.get("ambiguity") or "outcome not confirmed"
                uncertainty_notes.append(
                    f"Step {step['order']} ({step['action_type']}): {ambiguity}"
                )
        if inference.get("ambiguity"):
            uncertainty_notes.append(inference["ambiguity"])

        # Derive name/description from the inferred intent.
        intent = inference.get("intent", "demonstrated task")
        name = f"Continue {intent}" if not intent.startswith("unclear") else "Demonstrated workflow"
        description = (
            f"Learned from a {session_id} demonstration: {inference.get('reasoning', '')}"
        ).strip()

        return {
            "session_id": session_id,
            "name": name,
            "description": description,
            "steps": steps,
            "uncertainty_notes": uncertainty_notes,
            "inference": inference,
            "normalized_events": normalized,
        }


class SanitizedEvent:
    """A safe, LLM-friendly subset of a BrowserEvent (no secrets/form values)."""

    def __init__(self, event: BrowserEvent) -> None:
        self.event_id = event.id
        self.session_id = event.session_id
        self.timestamp = event.timestamp.isoformat()
        self.event_type = event.event_type
        self.page_url = event.page_url
        self.page_title = event.page_title
        self.target_summary = event.target_summary
        # safe_value may still contain user data; it is intentionally excluded
        # from anything sent to a model.


def sanitize(events: list[BrowserEvent]) -> list[SanitizedEvent]:
    """Return a reduced, secret-free view of events for any model call."""
    return [SanitizedEvent(e) for e in events]
