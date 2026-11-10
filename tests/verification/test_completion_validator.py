"""Tests for the CompletionValidator and ConfidenceAssessor components."""

from datetime import datetime, timezone

import pytest

from app.execution.verifier import (
    CheckpointDefinition,
    CheckpointManager,
    CheckpointType,
    CompletionValidator,
    ConfidenceAssessor,
    ConfidenceLevel,
    EvidenceItem,
    EvidenceType,
    ExpectedOutcome,
    NormalizedEvidence,
    OutcomeEvaluator,
    OutcomeMatch,
    OutcomeType,
    VerificationResult,
    VerificationStatus,
    WorkflowCompletionStatus,
)


@pytest.fixture
def validator():
    return CompletionValidator()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _make_step_result(
    step_id: str, status: VerificationStatus
) -> VerificationResult:
    return VerificationResult(
        execution_id="exec_1",
        step_id=step_id,
        status=status,
        confidence=ConfidenceLevel.HIGH,
        reason=f"Test: {status}",
    )


class TestCompletionValidator:

    def test_all_steps_verified(self, validator):
        results = {
            "s1": _make_step_result("s1", VerificationStatus.VERIFIED),
            "s2": _make_step_result("s2", VerificationStatus.VERIFIED),
            "s3": _make_step_result("s3", VerificationStatus.VERIFIED),
        }
        completion = validator.validate("exec_1", "wf_1", results, total_steps=3)
        assert completion.status == WorkflowCompletionStatus.COMPLETED
        assert completion.verified_steps == 3
        assert completion.pending_steps == 0

    def test_one_step_failed(self, validator):
        results = {
            "s1": _make_step_result("s1", VerificationStatus.VERIFIED),
            "s2": _make_step_result("s2", VerificationStatus.FAILED),
            "s3": _make_step_result("s3", VerificationStatus.VERIFIED),
        }
        completion = validator.validate("exec_1", "wf_1", results, total_steps=3)
        assert completion.status == WorkflowCompletionStatus.FAILED
        assert completion.failed_steps == 1

    def test_one_step_pending(self, validator):
        results = {
            "s1": _make_step_result("s1", VerificationStatus.VERIFIED),
            "s2": _make_step_result("s2", VerificationStatus.VERIFIED),
        }
        completion = validator.validate("exec_1", "wf_1", results, total_steps=3)
        assert completion.status == WorkflowCompletionStatus.PARTIALLY_COMPLETED
        assert completion.pending_steps == 1

    def test_required_checkpoint_incomplete(self, validator, now):
        results = {
            "s1": _make_step_result("s1", VerificationStatus.VERIFIED),
            "s2": _make_step_result("s2", VerificationStatus.VERIFIED),
        }
        cm = CheckpointManager()
        cp = CheckpointDefinition(
            checkpoint_id="cp_final",
            type=CheckpointType.WORKFLOW_COMPLETION,
            step_id="s2",
            required=True,
            expected_outcome=ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Final"),
        )
        cm.register(cp)
        # Checkpoint not evaluated → pending
        completion = validator.validate("exec_1", "wf_1", results, total_steps=2, checkpoint_manager=cm)
        assert completion.status == WorkflowCompletionStatus.NEEDS_REVIEW
        assert len(completion.unresolved_checkpoints) > 0

    def test_final_outcome_missing(self, validator):
        results = {}
        completion = validator.validate("exec_1", "wf_1", results, total_steps=3)
        assert completion.status == WorkflowCompletionStatus.PARTIALLY_COMPLETED
        assert completion.pending_steps == 3

    def test_blocking_issue_unresolved(self, validator):
        results = {
            "s1": _make_step_result("s1", VerificationStatus.VERIFIED),
            "s2": _make_step_result("s2", VerificationStatus.BLOCKED),
        }
        completion = validator.validate("exec_1", "wf_1", results, total_steps=2)
        assert completion.status == WorkflowCompletionStatus.BLOCKED

    def test_needs_review_step(self, validator):
        results = {
            "s1": _make_step_result("s1", VerificationStatus.VERIFIED),
            "s2": _make_step_result("s2", VerificationStatus.NEEDS_REVIEW),
        }
        completion = validator.validate("exec_1", "wf_1", results, total_steps=2)
        assert completion.status == WorkflowCompletionStatus.NEEDS_REVIEW


class TestConfidenceAssessor:

    def test_no_evidence_insufficient(self):
        assessor = ConfidenceAssessor()
        evidence = NormalizedEvidence(items=[])
        match = OutcomeMatch(matched=True, reason="")
        assert assessor.assess(evidence, match) == ConfidenceLevel.INSUFFICIENT

    def test_only_stale_evidence_insufficient(self, now):
        assessor = ConfidenceAssessor()
        evidence = NormalizedEvidence(items=[
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="X",
                observed_at=now,
                fresh=False,
            ),
        ])
        match = OutcomeMatch(matched=True, reason="")
        assert assessor.assess(evidence, match) == ConfidenceLevel.INSUFFICIENT

    def test_action_result_only_low_confidence(self, now):
        assessor = ConfidenceAssessor()
        evidence = NormalizedEvidence(items=[
            EvidenceItem(
                source="action_result",
                type=EvidenceType.ACTION_RESULT,
                value={"success": True},
                observed_at=now,
                fresh=True,
                observation_succeeded=True,
            ),
        ])
        match = OutcomeMatch(matched=True, reason="")
        assert assessor.assess(evidence, match) == ConfidenceLevel.LOW

    def test_multiple_sources_high_confidence(self, now):
        assessor = ConfidenceAssessor()
        evidence = NormalizedEvidence(items=[
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Lesson 3",
                observed_at=now,
                fresh=True,
                observation_succeeded=True,
            ),
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_URL,
                value="https://example.test/lesson-3",
                observed_at=now,
                fresh=True,
                observation_succeeded=True,
            ),
        ])
        match = OutcomeMatch(matched=True, reason="")
        assert assessor.assess(evidence, match) == ConfidenceLevel.HIGH

    def test_single_browser_evidence_medium(self, now):
        assessor = ConfidenceAssessor()
        evidence = NormalizedEvidence(items=[
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Lesson 3",
                observed_at=now,
                fresh=True,
                observation_succeeded=True,
            ),
        ])
        match = OutcomeMatch(matched=True, reason="")
        assert assessor.assess(evidence, match) == ConfidenceLevel.MEDIUM

