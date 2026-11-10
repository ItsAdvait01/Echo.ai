"""Tests for the VerificationEngine (CheckpointVerifier) component."""

from datetime import datetime, timezone, timedelta

import pytest

from app.execution.verifier import (
    CheckpointVerifier,
    ConfidenceLevel,
    ExpectedOutcome,
    OutcomeType,
    VerificationRequest,
    VerificationStatus,
)
from app.schemas.contracts import WorkflowStep


@pytest.fixture
def verifier():
    return CheckpointVerifier(staleness_seconds=60)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


class TestVerificationEngine:
    """Tests for the verification engine (via CheckpointVerifier)."""

    def test_verified_result(self, verifier, now):
        """Matching page title with fresh evidence produces VERIFIED."""
        step = WorkflowStep(
            id="step_1",
            order=1,
            action_type="navigate_to_known_page",
            expected_result={"type": "page_title", "value": "Python - Lesson 3"},
        )
        observed = {
            "page_title": "Python - Lesson 3",
            "page_url": "https://example.test/lesson-3",
            "timestamp": now.isoformat(),
        }
        result = verifier.verify(step, observed)
        assert result["status"] == VerificationStatus.VERIFIED
        assert result["confidence"] in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)

    def test_failed_result(self, verifier, now):
        """Mismatched page title with fresh evidence produces FAILED."""
        step = WorkflowStep(
            id="step_1",
            order=1,
            action_type="navigate_to_known_page",
            expected_result={"type": "page_title", "value": "Python - Lesson 3"},
        )
        observed = {
            "page_title": "Python - Lesson 2",
            "page_url": "https://example.test/lesson-2",
            "timestamp": now.isoformat(),
        }
        result = verifier.verify(step, observed)
        assert result["status"] == VerificationStatus.FAILED

    def test_needs_review_result(self, verifier, now):
        """Action success alone without outcome evidence produces NEEDS_REVIEW."""
        step = WorkflowStep(
            id="step_1",
            order=1,
            action_type="click_semantic_target",
            expected_result={"type": "page_title", "value": "Next Page"},
        )
        # Only action result, no page title evidence
        observed = {
            "action_result": {"success": True, "timestamp": now.isoformat()},
            "timestamp": now.isoformat(),
        }
        result = verifier.verify(step, observed)
        # With only an action result and no matching evidence type,
        # the title check finds no evidence → UNKNOWN or NEEDS_REVIEW
        assert result["status"] in (
            VerificationStatus.NEEDS_REVIEW,
            VerificationStatus.UNKNOWN,
        )

    def test_blocked_result_via_request(self, verifier, now):
        """A failed required checkpoint produces BLOCKED."""
        from app.execution.verifier import CheckpointDefinition, CheckpointType

        cp = CheckpointDefinition(
            checkpoint_id="cp_nav",
            type=CheckpointType.NAVIGATION,
            step_id="step_1",
            required=True,
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE, value="Expected Title"
            ),
        )
        verifier.register_checkpoint(cp)

        request = VerificationRequest(
            execution_id="exec_1",
            step_id="step_1",
            action_id="act_1",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE, value="Expected Title"
            ),
            browser_context={
                "page_title": "Wrong Title",
                "timestamp": now.isoformat(),
            },
            checkpoint_requirements=[{"checkpoint_id": "cp_nav"}],
        )
        result = verifier.verify_request(request)
        assert result.status == VerificationStatus.BLOCKED

    def test_unknown_result_no_evidence(self, verifier, now):
        """No evidence at all produces UNKNOWN."""
        step = WorkflowStep(
            id="step_1",
            order=1,
            action_type="navigate_to_known_page",
            expected_result={"type": "page_title", "value": "Something"},
        )
        result = verifier.verify(step, {})
        assert result["status"] == VerificationStatus.UNKNOWN

    def test_stale_evidence_rejection(self, verifier, now):
        """Stale evidence alone should not produce VERIFIED."""
        old_time = (now - timedelta(seconds=120)).isoformat()
        step = WorkflowStep(
            id="step_1",
            order=1,
            action_type="navigate_to_known_page",
            expected_result={"type": "page_title", "value": "Python - Lesson 3"},
        )
        observed = {
            "page_title": "Python - Lesson 3",
            "timestamp": old_time,
        }
        result = verifier.verify(step, observed)
        assert result["status"] != VerificationStatus.VERIFIED

    def test_action_success_without_outcome_evidence(self, verifier, now):
        """A click success alone must NOT produce VERIFIED."""
        step = WorkflowStep(
            id="step_1",
            order=1,
            action_type="click_semantic_target",
            expected_result={"type": "element_visible", "value": "next_section"},
        )
        observed = {
            "action_result": {"success": True, "timestamp": now.isoformat()},
            "timestamp": now.isoformat(),
        }
        result = verifier.verify(step, observed)
        assert result["status"] != VerificationStatus.VERIFIED

    def test_conflicting_evidence(self, verifier, now):
        """When evidence and expected outcome disagree, result is FAILED."""
        request = VerificationRequest(
            execution_id="exec_1",
            step_id="step_1",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.NAVIGATION,
                value="https://example.test/lesson-3",
            ),
            browser_context={
                "page_url": "https://example.test/lesson-2",
                "navigation_completed": True,
                "timestamp": now.isoformat(),
            },
        )
        result = verifier.verify_request(request)
        assert result.status == VerificationStatus.FAILED

    def test_no_expected_result_produces_unknown(self, verifier, now):
        """Step with empty expected_result produces UNKNOWN."""
        step = WorkflowStep(
            id="step_1",
            order=1,
            action_type="navigate_to_known_page",
            expected_result={},
        )
        result = verifier.verify(step, {"page_title": "Anything", "timestamp": now.isoformat()})
        assert result["status"] == VerificationStatus.UNKNOWN

    def test_duplicate_request_returns_cached(self, verifier, now):
        """Duplicate verification requests return the cached result."""
        request = VerificationRequest(
            execution_id="exec_dup",
            step_id="step_dup",
            action_id="act_dup",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE, value="Test"
            ),
            browser_context={
                "page_title": "Test",
                "timestamp": now.isoformat(),
            },
        )
        r1 = verifier.verify_request(request)
        r2 = verifier.verify_request(request)
        assert r1.status == r2.status
        assert r1.verified_at == r2.verified_at

    def test_verify_request_result_has_evidence_refs(self, verifier, now):
        request = VerificationRequest(
            execution_id="exec_ref",
            step_id="step_ref",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE, value="Ref Page"
            ),
            browser_context={
                "page_title": "Ref Page",
                "timestamp": now.isoformat(),
            },
        )
        result = verifier.verify_request(request)
        assert result.status == VerificationStatus.VERIFIED
        assert len(result.evidence_refs) > 0

    def test_verified_result_has_no_failure(self, verifier, now):
        request = VerificationRequest(
            execution_id="exec_ok",
            step_id="step_ok",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE, value="Good Page"
            ),
            browser_context={
                "page_title": "Good Page",
                "timestamp": now.isoformat(),
            },
        )
        result = verifier.verify_request(request)
        assert result.status == VerificationStatus.VERIFIED
        assert result.failure is None
        assert result.recovery is None

    def test_failed_result_has_failure_and_recovery(self, verifier, now):
        request = VerificationRequest(
            execution_id="exec_fail",
            step_id="step_fail",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE, value="Expected"
            ),
            browser_context={
                "page_title": "Wrong",
                "timestamp": now.isoformat(),
            },
        )
        result = verifier.verify_request(request)
        assert result.status == VerificationStatus.FAILED
        assert result.failure is not None
        assert result.recovery is not None
        assert "action" in result.recovery

