"""Checkpoint verification starter. U4 owns this module."""

from typing import Any

from app.schemas.contracts import WorkflowStep


class CheckpointVerifier:
    def verify(self, step: WorkflowStep, observed_state: dict[str, Any]) -> dict[str, Any]:
        """Return a typed pass/fail outcome; never advance progress itself."""
        raise NotImplementedError("U4: compare expected result with observed state")
