"""Workflow step extraction.

Converts grouped events and intent inferences into logical workflow steps that
map onto the A1-owned ``WorkflowStep`` contract. Observed facts and inferred
intent are kept separate inside each step's ``target`` and ``expected_result``
dicts.
"""

from typing import Any

from app.learning.confidence import ConfidenceModel
from app.learning.event_grouping import EventGroup

RISK_LOW = "low"
RISK_HIGH = "high"


class StepExtractor:
    """Builds step dictionaries from event groups."""

    def __init__(self) -> None:
        self.confidence = ConfidenceModel()
        self._counter = 0

    def extract(
        self,
        groups: list[EventGroup],
        workflow_inference: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return an ordered list of step dictionaries."""
        self._counter = 0
        steps: list[dict[str, Any]] = []
        group_inferences = workflow_inference.get("group_inferences", [])
        inference_by_group = {g["group_id"]: g for g in group_inferences}

        for index, group in enumerate(groups, start=1):
            group_inf = inference_by_group.get(group.group_id, {})
            step = self._build_step(index, group, group_inf)
            steps.append(step)
        return steps

    def _build_step(
        self, order: int, group: EventGroup, inference: dict[str, Any]
    ) -> dict[str, Any]:
        self._counter += 1
        step_id = f"step-{self._counter}"
        target = self._build_target(group, inference)
        expected_result = self._build_expected_result(group, inference)
        risk = self._assess_risk(inference, expected_result)
        return {
            "id": step_id,
            "order": order,
            "action_type": inference.get("intent", group.dominant_type or "interact"),
            "target": target,
            "expected_result": expected_result,
            "risk": risk,
        }

    def _build_target(self, group: EventGroup, inference: dict[str, Any]) -> dict[str, Any]:
        target_summary = group.primary_target or ""
        status = inference.get("status", "uncertain")
        confidence = inference.get(
            "confidence",
            self.confidence.score(status, evidence_count=len(group.event_ids)),
        )
        requires_confirmation = status in {"uncertain", "incomplete"}
        return {
            "group_id": group.group_id,
            "target_summary": target_summary,
            "page_url": group.primary_page_url,
            "page_title": group.primary_page_title,
            "dominant_event_type": group.dominant_type,
            "evidence_event_ids": list(group.event_ids),
            "confidence": confidence,
            "inference_status": status,
            "reason": inference.get("reasoning", ""),
            "requires_confirmation": requires_confirmation,
            "ambiguity": inference.get("ambiguity", ""),
        }

    def _build_expected_result(
        self, group: EventGroup, inference: dict[str, Any]
    ) -> dict[str, Any]:
        status = inference.get("status", "uncertain")
        uncertain = status in {"uncertain", "incomplete"}
        outcome_type = "state_change"
        value = group.primary_page_url or group.primary_page_title or ""
        if inference.get("intent", "").startswith("Mark"):
            outcome_type = "completion_indicator"
            value = "completion state reached"
        elif inference.get("intent", "").startswith("Move"):
            outcome_type = "page_transition"
            value = "next lesson page loaded"
        elif inference.get("intent", "").startswith("Open"):
            outcome_type = "page_title_contains"
            value = group.primary_page_title or "course page"
        preconditions = [
            {"type": "page_available", "description": "The learning platform page is accessible"}
        ]
        if uncertain:
            preconditions.append(
                {
                    "type": "target_visible",
                    "description": "The expected target must be visible before acting",
                }
            )
        return {
            "type": outcome_type,
            "value": value,
            "uncertain": uncertain,
            "page_url": group.primary_page_url,
            "page_title": group.primary_page_title,
            "preconditions": preconditions,
        }

    @staticmethod
    def _assess_risk(inference: dict[str, Any], expected_result: dict[str, Any]) -> str:
        status = inference.get("status", "uncertain")
        if status in {"uncertain", "incomplete"} or expected_result.get("uncertain"):
            return RISK_HIGH
        return RISK_LOW
