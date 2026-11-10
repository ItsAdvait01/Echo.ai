"""Tests for VerificationRequest validation and handling."""

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from app.execution.verifier import (
    CheckpointVerifier,
    ExpectedOutcome,
    OutcomeType,
    VerificationRequest,
    VerificationStatus,
)


@pytest.fixture
def verifier():
    return CheckpointVerifier()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


class TestRequestHandler:
    """A. Request handler tests."""

    def test_valid_request(self, verifier, now):
        req = VerificationRequest(
            execution_id="exec_123",
            workflow_id="wf_abc",
            workflow_version="1.0.0",
            step_id="step_001",
            action_id="act_001",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE,
                value="Python - Lesson 3",
            ),
            browser_context={
                "page_title": "Python - Lesson 3",
                "timestamp": now.isoformat(),
            },
        )
        res = verifier.verify_request(req)
        assert res.execution_id == "exec_123"
        assert res.step_id == "step_001"
        assert res.action_id == "act_001"
        assert res.status == VerificationStatus.VERIFIED

    def test_missing_execution_id_raises_validation_error(self):
        with pytest.raises(ValidationError):
            VerificationRequest(  # type: ignore[call-arg]
                step_id="step_001",
                expected_outcome=ExpectedOutcome(
                    type=OutcomeType.PAGE_TITLE,
                    value="Title",
                ),
            )

    def test_missing_step_id_raises_validation_error(self):
        with pytest.raises(ValidationError):
            VerificationRequest(  # type: ignore[call-arg]
                execution_id="exec_123",
                expected_outcome=ExpectedOutcome(
                    type=OutcomeType.PAGE_TITLE,
                    value="Title",
                ),
            )

    def test_missing_expected_outcome_raises_validation_error(self):
        with pytest.raises(ValidationError):
            VerificationRequest(  # type: ignore[call-arg]
                execution_id="exec_123",
                step_id="step_001",
            )

    def test_invalid_schema_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            VerificationRequest(
                execution_id="exec_123",
                step_id="step_001",
                expected_outcome=ExpectedOutcome(
                    type=OutcomeType.PAGE_TITLE,
                    value="Title",
                ),
                unrecognized_field="forbidden_value",  # type: ignore[call-arg]
            )

    def test_duplicate_request_idempotency(self, verifier, now):
        req1 = VerificationRequest(
            execution_id="exec_idem",
            step_id="step_idem",
            action_id="act_idem",
            expected_outcome=ExpectedOutcome(
                type=OutcomeType.PAGE_TITLE,
                value="Home",
            ),
            browser_context={
                "page_title": "Home",
                "timestamp": now.isoformat(),
            },
        )
        res1 = verifier.verify_request(req1)
        res2 = verifier.verify_request(req1)

        assert res1.execution_id == res2.execution_id
        assert res1.step_id == res2.step_id
        assert res1.verified_at == res2.verified_at
        assert res1.status == res2.status
