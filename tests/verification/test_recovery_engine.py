"""Tests for the RecoveryPolicy and RecoveryEngine components."""

from datetime import datetime, timezone

import pytest

from app.execution.recovery import (
    RecoveryAction,
    RecoveryEngine,
    RecoveryPolicy,
    RecoveryRecommendation,
)
from app.execution.verifier import (
    ConfidenceLevel,
    FailureCode,
    FailureDetail,
    VerificationResult,
    VerificationStatus,
)


@pytest.fixture
def policy():
    return RecoveryPolicy(max_retries=2)


@pytest.fixture
def engine():
    return RecoveryEngine(RecoveryPolicy(max_retries=2))


def _make_result(
    status: VerificationStatus,
    failure: FailureDetail | None = None,
    execution_id: str = "exec_1",
    step_id: str = "step_1",
) -> VerificationResult:
    return VerificationResult(
        execution_id=execution_id,
        step_id=step_id,
        status=status,
        confidence=ConfidenceLevel.MEDIUM,
        reason="Test reason.",
        failure=failure,
    )


class TestRecoveryPolicy:

    def test_should_retry_within_limit(self, policy):
        assert policy.should_retry(attempt=0, retryable=True) is True
        assert policy.should_retry(attempt=1, retryable=True) is True

    def test_should_not_retry_at_limit(self, policy):
        assert policy.should_retry(attempt=2, retryable=True) is False

    def test_should_not_retry_non_retryable(self, policy):
        assert policy.should_retry(attempt=0, retryable=False) is False

    def test_should_not_retry_ambiguous_target(self, policy):
        assert policy.should_retry(attempt=0, ambiguous_target=True, retryable=True) is False

    def test_should_not_retry_with_side_effects(self, policy):
        assert policy.should_retry(attempt=0, has_side_effects=True, retryable=True) is False

    def test_should_not_retry_low_float_confidence(self, policy):
        assert policy.should_retry(attempt=0, confidence=0.3, retryable=True) is False

    def test_should_not_retry_insufficient_confidence(self, policy):
        assert policy.should_retry(
            attempt=0, confidence=ConfidenceLevel.INSUFFICIENT, retryable=True
        ) is False

    def test_record_and_get_attempts(self, policy):
        assert policy.get_attempts("key1") == 0
        policy.record_attempt("key1")
        assert policy.get_attempts("key1") == 1
        policy.record_attempt("key1")
        assert policy.get_attempts("key1") == 2

    def test_remaining_retries(self, policy):
        assert policy.remaining_retries("key1") == 2
        policy.record_attempt("key1")
        assert policy.remaining_retries("key1") == 1

    def test_reset(self, policy):
        policy.record_attempt("k")
        policy.reset("k")
        assert policy.get_attempts("k") == 0

    def test_reset_all(self, policy):
        policy.record_attempt("a")
        policy.record_attempt("b")
        policy.reset()
        assert policy.get_attempts("a") == 0
        assert policy.get_attempts("b") == 0


class TestRecoveryEngine:

    def test_safe_retry_recommendation(self, engine):
        failure = FailureDetail(
            code=FailureCode.NAVIGATION_FAILED,
            message="Navigation failed.",
            retryable=True,
        )
        result = _make_result(VerificationStatus.FAILED, failure)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.RETRY_SAFE
        assert rec.max_retries_remaining > 0

    def test_retry_limit_reached(self, engine):
        failure = FailureDetail(
            code=FailureCode.NAVIGATION_FAILED,
            message="Navigation failed.",
            retryable=True,
        )
        result = _make_result(VerificationStatus.FAILED, failure)
        # Exhaust retries
        engine.recommend(result)  # uses 1 retry
        engine.recommend(result)  # uses 2nd retry
        rec = engine.recommend(result)  # limit reached
        assert rec.action == RecoveryAction.PAUSE
        assert rec.requires_user is True

    def test_non_retryable_failure(self, engine):
        failure = FailureDetail(
            code=FailureCode.PAGE_STATE_MISMATCH,
            message="State mismatch.",
            retryable=False,
        )
        result = _make_result(VerificationStatus.FAILED, failure)
        rec = engine.recommend(result)
        # Non-retryable: should reinspect or pause, not retry
        assert rec.action != RecoveryAction.RETRY_SAFE

    def test_stale_evidence_recommends_reinspect(self, engine):
        failure = FailureDetail(
            code=FailureCode.STALE_EVIDENCE,
            message="Evidence stale.",
            retryable=True,
        )
        result = _make_result(VerificationStatus.FAILED, failure)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.REINSPECT

    def test_browser_session_unavailable(self, engine):
        failure = FailureDetail(
            code=FailureCode.BROWSER_SESSION_UNAVAILABLE,
            message="Session lost.",
            retryable=False,
        )
        result = _make_result(VerificationStatus.FAILED, failure)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.PAUSE
        assert rec.requires_user is True

    def test_ambiguous_target_asks_user(self, engine):
        failure = FailureDetail(
            code=FailureCode.USER_CONFIRMATION_REQUIRED,
            message="Ambiguous target.",
            retryable=False,
        )
        result = _make_result(VerificationStatus.NEEDS_REVIEW, failure)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.ASK_USER
        assert rec.requires_user is True

    def test_blocking_policy_violation_aborts(self, engine):
        result = _make_result(VerificationStatus.BLOCKED)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.ABORT

    def test_user_cancellation_aborts(self, engine):
        result = _make_result(VerificationStatus.FAILED)
        rec = engine.recommend(result, user_cancelled=True)
        assert rec.action == RecoveryAction.ABORT

    def test_browser_session_not_available(self, engine):
        result = _make_result(VerificationStatus.FAILED)
        rec = engine.recommend(result, browser_session_available=False)
        assert rec.action == RecoveryAction.PAUSE

    def test_unsupported_operation_aborts(self, engine):
        failure = FailureDetail(
            code=FailureCode.UNSUPPORTED_OPERATION,
            message="Not supported.",
            retryable=False,
        )
        result = _make_result(VerificationStatus.FAILED, failure)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.ABORT

    def test_precondition_failed_replans(self, engine):
        failure = FailureDetail(
            code=FailureCode.PRECONDITION_FAILED,
            message="Precondition not met.",
            retryable=False,
        )
        result = _make_result(VerificationStatus.FAILED, failure)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.REPLAN

    def test_evidence_conflict_pauses(self, engine):
        failure = FailureDetail(
            code=FailureCode.EVIDENCE_CONFLICT,
            message="Conflicting evidence.",
            retryable=False,
        )
        result = _make_result(VerificationStatus.FAILED, failure)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.PAUSE

    def test_needs_review_without_failure_reinspects(self, engine):
        result = _make_result(VerificationStatus.NEEDS_REVIEW)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.REINSPECT

    def test_unknown_without_failure_reinspects(self, engine):
        result = _make_result(VerificationStatus.UNKNOWN)
        rec = engine.recommend(result)
        assert rec.action == RecoveryAction.REINSPECT

    def test_verified_result_no_recovery_needed(self, engine):
        result = _make_result(VerificationStatus.VERIFIED)
        rec = engine.recommend(result)
        assert rec.reason.startswith("Step is already verified")

