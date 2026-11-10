"""Tests for the CheckpointManager component."""

from datetime import datetime, timezone

import pytest

from app.execution.verifier import (
    CheckpointDefinition,
    CheckpointManager,
    CheckpointResult,
    CheckpointStatus,
    CheckpointType,
    EvidenceItem,
    EvidenceType,
    ExpectedOutcome,
    NormalizedEvidence,
    OutcomeEvaluator,
    OutcomeType,
)


@pytest.fixture
def manager():
    return CheckpointManager()


@pytest.fixture
def evaluator():
    return OutcomeEvaluator()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _make_evidence(items: list[EvidenceItem]) -> NormalizedEvidence:
    return NormalizedEvidence(items=items)


class TestCheckpointManager:

    def test_required_checkpoint_passed(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_1",
            type=CheckpointType.POSTCONDITION,
            step_id="step_1",
            required=True,
            expected_outcome=ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Target Page"),
        )
        manager.register(cp)
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Target Page",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = manager.evaluate("cp_1", evidence, evaluator)
        assert result.status == CheckpointStatus.PASSED

    def test_required_checkpoint_failed(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_2",
            type=CheckpointType.POSTCONDITION,
            step_id="step_1",
            required=True,
            expected_outcome=ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Expected"),
        )
        manager.register(cp)
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Wrong Page",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = manager.evaluate("cp_2", evidence, evaluator)
        assert result.status == CheckpointStatus.FAILED

    def test_optional_checkpoint(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_opt",
            type=CheckpointType.POSTCONDITION,
            step_id="step_1",
            required=False,
            expected_outcome=ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Optional"),
        )
        manager.register(cp)
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Different",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = manager.evaluate("cp_opt", evidence, evaluator)
        # Optional checkpoint can fail without blocking
        assert result.status == CheckpointStatus.FAILED
        # But all_required_passed should still be True
        assert manager.all_required_passed() is True

    def test_pending_checkpoint(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_pending",
            type=CheckpointType.POSTCONDITION,
            step_id="step_1",
            required=True,
        )
        manager.register(cp)
        # Not evaluated yet → pending
        assert "cp_pending" in manager.pending_checkpoints()

    def test_user_approval_required(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_approval",
            type=CheckpointType.USER_APPROVAL,
            step_id="step_1",
            required=True,
        )
        manager.register(cp)
        # No user confirmation evidence
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Some Page",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = manager.evaluate("cp_approval", evidence, evaluator)
        assert result.status == CheckpointStatus.PENDING

    def test_user_approval_granted(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_approval",
            type=CheckpointType.USER_APPROVAL,
            step_id="step_1",
            required=True,
        )
        manager.register(cp)
        evidence = _make_evidence([
            EvidenceItem(
                source="user",
                type=EvidenceType.USER_CONFIRMATION,
                value=True,
                observed_at=now,
                fresh=True,
                observation_succeeded=True,
            ),
        ])
        result = manager.evaluate("cp_approval", evidence, evaluator)
        assert result.status == CheckpointStatus.PASSED

    def test_dependent_step_blocked(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_block",
            type=CheckpointType.POSTCONDITION,
            step_id="step_1",
            required=True,
            expected_outcome=ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Expected"),
        )
        manager.register(cp)
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Wrong",
                observed_at=now,
                fresh=True,
            ),
        ])
        manager.evaluate("cp_block", evidence, evaluator)
        assert manager.can_proceed_past("step_1") is False

    def test_all_required_passed(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_pass",
            type=CheckpointType.POSTCONDITION,
            step_id="step_1",
            required=True,
            expected_outcome=ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Pass"),
        )
        manager.register(cp)
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Pass",
                observed_at=now,
                fresh=True,
            ),
        ])
        manager.evaluate("cp_pass", evidence, evaluator)
        assert manager.all_required_passed() is True

    def test_unregistered_checkpoint_fails(self, manager, evaluator, now):
        evidence = _make_evidence([])
        result = manager.evaluate("nonexistent", evidence, evaluator)
        assert result.status == CheckpointStatus.FAILED

    def test_checkpoint_without_expected_outcome_passes_with_evidence(self, manager, evaluator, now):
        cp = CheckpointDefinition(
            checkpoint_id="cp_no_exp",
            type=CheckpointType.EXECUTION_BOUNDARY,
            step_id="step_1",
            required=True,
        )
        manager.register(cp)
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Any",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = manager.evaluate("cp_no_exp", evidence, evaluator)
        assert result.status == CheckpointStatus.PASSED

