"""End-to-end tests for the complete Verification & Recovery lifecycle."""

from datetime import datetime, timezone
import pytest

from app.execution.verifier import (
    CheckpointDefinition,
    CheckpointType,
    CheckpointVerifier,
    ConfidenceLevel,
    ExpectedOutcome,
    FailureCode,
    OutcomeType,
    VerificationRequest,
    VerificationStatus,
    WorkflowCompletionStatus,
)
from app.execution.recovery import RecoveryAction, RecoveryEngine
from app.schemas.contracts import WorkflowStep


@pytest.fixture
def verifier():
    return CheckpointVerifier(staleness_seconds=60)


@pytest.fixture
def recovery_engine():
    return RecoveryEngine()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


class TestVerificationLifecycleE2E:
    """H. End-to-end full verification and recovery lifecycle testing."""

    def test_e2e_python_course_navigation_success(self, verifier, recovery_engine, now):
        """Complete successful run:
        Step 1: Open Course -> VERIFIED
        Step 2: Checkpoint Precondition passed
        Step 3: Open Lesson 3 -> VERIFIED
        Completion: Verified 2/2 steps -> COMPLETED
        """
        # Register a navigation checkpoint after Step 2
        cp = CheckpointDefinition(
            checkpoint_id="cp_course_ready",
            type=CheckpointType.PRECONDITION,
            step_id="step_2",
            required=True,
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE,
                value="Python - Lesson 3",
            ),
        )
        verifier.register_checkpoint(cp)

        # 1. Step 1: Open course
        req1 = VerificationRequest(
            execution_id="exec_course_1",
            workflow_id="wf_python_nav",
            workflow_version="1.0.0",
            step_id="step_1",
            action_id="act_nav_course",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.NAVIGATION,
                value="https://example.test/course",
            ),
            browser_context={
                "page_url": "https://example.test/course",
                "navigation_completed": True,
                "timestamp": now.isoformat(),
            },
        )
        res1 = verifier.verify_request(req1)
        assert res1.status == VerificationStatus.VERIFIED
        assert res1.is_success()
        assert res1.allows_continuation()
        assert res1.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)
        assert res1.failure is None
        assert len(res1.evidence_refs) > 0

        # 2. Step 2: Open Lesson 3
        req2 = VerificationRequest(
            execution_id="exec_course_1",
            workflow_id="wf_python_nav",
            workflow_version="1.0.0",
            step_id="step_2",
            action_id="act_click_lesson3",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE,
                value="Python - Lesson 3",
            ),
            browser_context={
                "page_title": "Python - Lesson 3",
                "page_url": "https://example.test/course/lesson-3",
                "visible_elements": ["lesson_content", "next_button"],
                "text_content": "Welcome to Python Lesson 3",
                "timestamp": now.isoformat(),
            },
            checkpoint_requirements=[{"checkpoint_id": "cp_course_ready"}],
        )
        res2 = verifier.verify_request(req2)
        assert res2.status == VerificationStatus.VERIFIED
        assert res2.is_success()
        assert len(res2.checkpoints) == 1
        assert res2.checkpoints[0].status == "passed"

        # 3. Completion Validation
        completion = verifier.verify_completion(
            execution_id="exec_course_1",
            workflow_id="wf_python_nav",
            total_steps=2,
        )
        assert completion.status == WorkflowCompletionStatus.COMPLETED
        assert completion.verified_steps == 2
        assert completion.failed_steps == 0
        assert completion.pending_steps == 0
        assert len(completion.unresolved_checkpoints) == 0

    def test_e2e_python_course_failure_and_recovery(self, verifier, recovery_engine, now):
        """Failure scenario:
        Next button clicked, but browser stayed on Lesson 2.
        Verification must detect PAGE_STATE_MISMATCH or title mismatch,
        return FAILED, and recommend safe recovery.
        """
        step = WorkflowStep(
            id="step_next_lesson",
            order=2,
            action_type="click_semantic_target",
            expected_result={
                "type": "page_title",
                "value": "Python - Lesson 3",
            },
        )

        observed_browser_stuck = {
            "page_title": "Python - Lesson 2",
            "page_url": "https://example.test/course/lesson-2",
            "action_result": {
                "success": True,  # Click succeeded! But page didn't advance
                "timestamp": now.isoformat(),
            },
            "timestamp": now.isoformat(),
        }

        # Step verification using simple WorkflowStep contract
        result_dict = verifier.verify(step, observed_browser_stuck)

        assert result_dict["status"] == VerificationStatus.FAILED
        assert result_dict["failure"] is not None
        assert result_dict["failure"]["code"] == FailureCode.NAVIGATION_FAILED or result_dict["failure"]["code"] == FailureCode.PAGE_STATE_MISMATCH or result_dict["failure"]["code"] == FailureCode.UNKNOWN_FAILURE or "mismatch" in result_dict["reason"].lower()

        # Attempt recovery via verifier result directly
        from app.execution.verifier import VerificationResult
        v_res = VerificationResult.model_validate(result_dict)
        rec2 = recovery_engine.recommend(v_res)

        assert rec2.action in (RecoveryAction.REINSPECT, RecoveryAction.RETRY_SAFE, RecoveryAction.PAUSE)

    def test_e2e_cannot_complete_with_pending_steps(self, verifier, now):
        """Workflow cannot be COMPLETED if only 1 of 3 steps was verified."""
        req1 = VerificationRequest(
            execution_id="exec_partial",
            workflow_id="wf_nav",
            step_id="step_1",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE,
                value="Lesson 1",
            ),
            browser_context={
                "page_title": "Lesson 1",
                "timestamp": now.isoformat(),
            },
        )
        verifier.verify_request(req1)

        completion = verifier.verify_completion(
            execution_id="exec_partial",
            workflow_id="wf_nav",
            total_steps=3,
        )
        assert completion.status == WorkflowCompletionStatus.PARTIALLY_COMPLETED
        assert completion.verified_steps == 1
        assert completion.pending_steps == 2
