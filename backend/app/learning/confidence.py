"""Confidence and evidence model for the U2 workflow learner.

A simple MVP confidence model. Scores are deliberately coarse (rounded to two
decimals) and are internal estimates, not guarantees of correctness.
"""

from typing import Any

# Inference statuses keep observed facts separate from inferred intent.
DIRECTLY_OBSERVED = "directly_observed"
SUPPORTED = "supported"
UNCERTAIN = "uncertain"
INCOMPLETE = "incomplete"

VALID_STATUSES = {
    DIRECTLY_OBSERVED,
    SUPPORTED,
    UNCERTAIN,
    INCOMPLETE,
}


class ConfidenceModel:
    """Assigns confidence and an inference status to a workflow step."""

    # Base confidence per inference status (coarse, not over-precise).
    BASE = {
        DIRECTLY_OBSERVED: 0.95,
        SUPPORTED: 0.80,
        UNCERTAIN: 0.55,
        INCOMPLETE: 0.35,
    }

    def score(
        self,
        status: str,
        *,
        evidence_count: int = 0,
        has_page_transition: bool = False,
        has_confirmation: bool = False,
    ) -> float:
        """Return a coarse confidence in [0, 1] rounded to two decimals."""
        if status not in VALID_STATUSES:
            status = UNCERTAIN
        base = self.BASE[status]
        adjustment = 0.0
        if evidence_count >= 2:
            adjustment += 0.05
        if has_page_transition:
            adjustment += 0.05
        if has_confirmation:
            adjustment += 0.05
        # Damp repeated evidence so scores do not look artificially precise.
        adjustment = min(adjustment, 0.10)
        return round(min(1.0, base + adjustment), 2)

    def step_meta(
        self,
        status: str,
        *,
        evidence_event_ids: list[str],
        reason: str,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Build the confidence/evidence block stored on a step's target dict."""
        if confidence is None:
            confidence = self.score(
                status,
                evidence_count=len(evidence_event_ids),
            )
        return {
            "confidence": confidence,
            "evidence_event_ids": list(evidence_event_ids),
            "inference_status": status,
            "reason": reason,
        }
