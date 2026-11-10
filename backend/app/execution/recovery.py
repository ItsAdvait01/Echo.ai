"""
Bounded retry, recovery recommendation, and safe-stop policy engine.

U4 owns this module. It provides recovery recommendations to U3 — it does
NOT execute recovery actions itself.

Components:
    - RecoveryAction enum
    - RecoveryRecommendation model
    - RecoveryPolicy: bounded retry and safe-stop decisions
    - RecoveryEngine: full recovery recommendation engine
"""

from __future__ import annotations

import logging
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.execution.verifier import (
    ConfidenceLevel,
    FailureCode,
    FailureDetail,
    VerificationResult,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recovery action enum
# ---------------------------------------------------------------------------


class RecoveryAction(StrEnum):
    """Possible recovery actions that U4 can recommend to U3."""

    RETRY_SAFE = "RETRY_SAFE"
    REINSPECT = "REINSPECT"
    REPLAN = "REPLAN"
    PAUSE = "PAUSE"
    ASK_USER = "ASK_USER"
    ABORT = "ABORT"


# ---------------------------------------------------------------------------
# Recovery recommendation model
# ---------------------------------------------------------------------------


class RecoveryRecommendation(BaseModel):
    """Structured recovery recommendation returned to U3."""

    model_config = ConfigDict(extra="forbid")

    action: RecoveryAction
    reason: str
    requires_user: bool = False
    max_retries_remaining: int = 0
    execution_id: str = ""
    step_id: str = ""
    original_failure: FailureDetail | None = None
    preserve_evidence: bool = True


# ---------------------------------------------------------------------------
# Recovery policy
# ---------------------------------------------------------------------------


_DEFAULT_MAX_RETRIES = 2


class RecoveryPolicy:
    """Bounded retry and safe-stop policy.

    Rules:
    - Maximum two safe retries (configurable).
    - No infinite retry loops.
    - No automatic retry of non-idempotent actions.
    - No retry after an uncertain irreversible side effect.
    - A failed or unknown verification must never be silently promoted to success.
    """

    def __init__(self, max_retries: int = _DEFAULT_MAX_RETRIES) -> None:
        self.max_retries = max_retries
        self._attempt_counts: dict[str, int] = {}

    def should_retry(
        self,
        attempt: int,
        confidence: float | ConfidenceLevel = ConfidenceLevel.MEDIUM,
        ambiguous_target: bool = False,
        retryable: bool = True,
        has_side_effects: bool = False,
    ) -> bool:
        """Determine whether a retry is safe.

        Parameters
        ----------
        attempt:
            Current attempt count (1-based).
        confidence:
            Evidence confidence.  Accepts a float for backward compatibility
            with the original starter or a ``ConfidenceLevel`` enum value.
        ambiguous_target:
            Whether the action target is ambiguous.
        retryable:
            Whether the failure has been classified as retryable.
        has_side_effects:
            Whether the action may have produced irreversible side effects.
        """
        if ambiguous_target:
            return False

        if has_side_effects:
            return False

        if not retryable:
            return False

        # Handle float confidence for backward compatibility
        if isinstance(confidence, (int, float)):
            if confidence < 0.5:
                return False
        elif isinstance(confidence, ConfidenceLevel):
            if confidence == ConfidenceLevel.INSUFFICIENT:
                return False

        return attempt < self.max_retries

    def record_attempt(self, key: str) -> int:
        """Record an attempt and return the current count."""
        self._attempt_counts[key] = self._attempt_counts.get(key, 0) + 1
        return self._attempt_counts[key]

    def get_attempts(self, key: str) -> int:
        """Return the number of recorded attempts for a key."""
        return self._attempt_counts.get(key, 0)

    def remaining_retries(self, key: str) -> int:
        """Return the number of retries remaining for a key."""
        used = self._attempt_counts.get(key, 0)
        return max(0, self.max_retries - used)

    def reset(self, key: str | None = None) -> None:
        """Reset attempt counts.  If key is None, reset all."""
        if key is None:
            self._attempt_counts.clear()
        else:
            self._attempt_counts.pop(key, None)


# ---------------------------------------------------------------------------
# Recovery engine
# ---------------------------------------------------------------------------


class RecoveryEngine:
    """Generate structured recovery recommendations based on verification
    outcomes, failure classifications, and recovery policy.

    U4 recommends.  U3 decides whether and how to act on the recommendation.
    """

    def __init__(self, policy: RecoveryPolicy | None = None) -> None:
        self._policy = policy or RecoveryPolicy()

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    def recommend(
        self,
        verification_result: VerificationResult,
        has_side_effects: bool = False,
        user_cancelled: bool = False,
        browser_session_available: bool = True,
    ) -> RecoveryRecommendation:
        """Generate a recovery recommendation based on the verification result.

        Parameters
        ----------
        verification_result:
            The outcome of the verification engine.
        has_side_effects:
            Whether the failed action may have produced irreversible side effects.
        user_cancelled:
            Whether the user has explicitly cancelled execution.
        browser_session_available:
            Whether the browser session is still available.
        """

        execution_id = verification_result.execution_id
        step_id = verification_result.step_id
        failure = verification_result.failure
        status = verification_result.status

        # If user cancelled, always abort
        if user_cancelled:
            logger.info("User cancelled execution — recommending ABORT.")
            return RecoveryRecommendation(
                action=RecoveryAction.ABORT,
                reason="User explicitly cancelled execution.",
                requires_user=False,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        # If browser session is gone, pause
        if not browser_session_available:
            logger.info("Browser session unavailable — recommending PAUSE.")
            return RecoveryRecommendation(
                action=RecoveryAction.PAUSE,
                reason="Browser session is no longer available.",
                requires_user=True,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        # If verified, no recovery needed (shouldn't be called, but handle gracefully)
        if status == VerificationStatus.VERIFIED:
            return RecoveryRecommendation(
                action=RecoveryAction.RETRY_SAFE,  # No-op: step is verified
                reason="Step is already verified; no recovery needed.",
                execution_id=execution_id,
                step_id=step_id,
            )

        # BLOCKED → ABORT
        if status == VerificationStatus.BLOCKED:
            return RecoveryRecommendation(
                action=RecoveryAction.ABORT,
                reason="Execution is blocked by a policy or precondition violation.",
                requires_user=True,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        # Derive recovery from failure classification
        if failure is not None:
            return self._recommend_from_failure(
                failure=failure,
                execution_id=execution_id,
                step_id=step_id,
                has_side_effects=has_side_effects,
                status=status,
            )

        # NEEDS_REVIEW without a classified failure
        if status == VerificationStatus.NEEDS_REVIEW:
            return RecoveryRecommendation(
                action=RecoveryAction.REINSPECT,
                reason="Verification result needs review; reinspecting browser state.",
                execution_id=execution_id,
                step_id=step_id,
            )

        # UNKNOWN — insufficient evidence
        if status == VerificationStatus.UNKNOWN:
            return RecoveryRecommendation(
                action=RecoveryAction.REINSPECT,
                reason="Verification result is unknown due to insufficient evidence.",
                execution_id=execution_id,
                step_id=step_id,
            )

        # FAILED without failure detail
        return RecoveryRecommendation(
            action=RecoveryAction.PAUSE,
            reason="Verification failed but no failure detail available.",
            requires_user=True,
            execution_id=execution_id,
            step_id=step_id,
        )

    def _recommend_from_failure(
        self,
        failure: FailureDetail,
        execution_id: str,
        step_id: str,
        has_side_effects: bool,
        status: VerificationStatus,
    ) -> RecoveryRecommendation:
        """Generate recommendation from a classified failure."""

        retry_key = f"{execution_id}:{step_id}"
        attempt = self._policy.get_attempts(retry_key)

        # Check if retry is safe
        can_retry = self._policy.should_retry(
            attempt=attempt,
            confidence=ConfidenceLevel.MEDIUM,
            ambiguous_target=failure.code == FailureCode.ELEMENT_NOT_FOUND,
            retryable=failure.retryable,
            has_side_effects=has_side_effects,
        )

        # Map failure codes to recovery actions
        if failure.code == FailureCode.UNSUPPORTED_OPERATION:
            return RecoveryRecommendation(
                action=RecoveryAction.ABORT,
                reason=f"Unsupported operation: {failure.message}",
                requires_user=True,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        if failure.code in (
            FailureCode.PERMISSION_REQUIRED,
            FailureCode.USER_CONFIRMATION_REQUIRED,
        ):
            return RecoveryRecommendation(
                action=RecoveryAction.ASK_USER,
                reason=f"User action required: {failure.message}",
                requires_user=True,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        if failure.code == FailureCode.EVIDENCE_CONFLICT:
            return RecoveryRecommendation(
                action=RecoveryAction.PAUSE,
                reason="Conflicting evidence detected; cannot determine outcome safely.",
                requires_user=True,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        if failure.code == FailureCode.BROWSER_SESSION_UNAVAILABLE:
            return RecoveryRecommendation(
                action=RecoveryAction.PAUSE,
                reason="Browser session is unavailable.",
                requires_user=True,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        if failure.code == FailureCode.PRECONDITION_FAILED:
            return RecoveryRecommendation(
                action=RecoveryAction.REPLAN,
                reason="Precondition failed; replanning may resolve the issue.",
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        if failure.code == FailureCode.STALE_EVIDENCE:
            return RecoveryRecommendation(
                action=RecoveryAction.REINSPECT,
                reason="Evidence is stale; need to re-observe browser state.",
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        # Retryable failures with budget remaining
        if can_retry and failure.retryable:
            self._policy.record_attempt(retry_key)
            remaining = self._policy.remaining_retries(retry_key)
            return RecoveryRecommendation(
                action=RecoveryAction.RETRY_SAFE,
                reason=f"Retryable failure ({failure.code}); {remaining} retries remaining.",
                max_retries_remaining=remaining,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        # Retry budget exhausted or not retryable
        if failure.retryable and not can_retry:
            return RecoveryRecommendation(
                action=RecoveryAction.PAUSE,
                reason=f"Retry limit reached for step {step_id}.",
                requires_user=True,
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        # Default: reinspect for minor failures, pause otherwise
        if failure.code in (
            FailureCode.ELEMENT_NOT_FOUND,
            FailureCode.PAGE_STATE_MISMATCH,
            FailureCode.EXPECTED_TEXT_MISSING,
            FailureCode.NAVIGATION_FAILED,
        ):
            return RecoveryRecommendation(
                action=RecoveryAction.REINSPECT,
                reason=f"Failure ({failure.code}); reinspecting browser state.",
                execution_id=execution_id,
                step_id=step_id,
                original_failure=failure,
            )

        return RecoveryRecommendation(
            action=RecoveryAction.PAUSE,
            reason=f"Unhandled failure ({failure.code}): {failure.message}",
            requires_user=True,
            execution_id=execution_id,
            step_id=step_id,
            original_failure=failure,
        )
