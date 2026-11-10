"""Tests for the FailureClassifier component."""

from datetime import datetime, timezone

import pytest

from app.execution.verifier import (
    EvidenceItem,
    EvidenceType,
    FailureClassifier,
    FailureCode,
    NormalizedEvidence,
    OutcomeMatch,
)


@pytest.fixture
def classifier():
    return FailureClassifier()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _make_evidence(items: list[EvidenceItem]) -> NormalizedEvidence:
    return NormalizedEvidence(items=items)


class TestFailureClassifier:

    def test_stale_evidence_failure(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="Only stale evidence.",
            checks_failed=["stale_evidence"],
        )
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence, execution_id="e1", step_id="s1")
        assert result.code == FailureCode.STALE_EVIDENCE
        assert result.retryable is True

    def test_element_not_found_failure(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="Element not found.",
            checks_failed=["element_not_found"],
        )
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence)
        assert result.code == FailureCode.ELEMENT_NOT_FOUND
        assert result.retryable is True

    def test_navigation_failed(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="Navigation failed.",
            checks_failed=["navigation_incomplete"],
        )
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence)
        assert result.code == FailureCode.NAVIGATION_FAILED
        assert result.retryable is True

    def test_page_state_mismatch(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="State mismatch.",
            checks_failed=["page_state_mismatch"],
        )
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence)
        assert result.code == FailureCode.PAGE_STATE_MISMATCH
        assert result.retryable is False

    def test_text_missing(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="Text not found.",
            checks_failed=["text_missing"],
        )
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence)
        assert result.code == FailureCode.EXPECTED_TEXT_MISSING

    def test_user_confirmation_required(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="User confirmation missing.",
            checks_failed=["user_confirmation_missing"],
        )
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence)
        assert result.code == FailureCode.USER_CONFIRMATION_REQUIRED

    def test_evidence_conflict(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="Conflict.",
            checks_failed=["other_check"],
        )
        # Two conflicting values for the same evidence type
        evidence = _make_evidence([
            EvidenceItem(
                source="a",
                type=EvidenceType.PAGE_TITLE,
                value="Title A",
                observed_at=now,
                fresh=True,
            ),
            EvidenceItem(
                source="b",
                type=EvidenceType.PAGE_TITLE,
                value="Title B",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = classifier.classify(match, evidence)
        assert result.code == FailureCode.EVIDENCE_CONFLICT

    def test_browser_session_unavailable(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="No evidence.",
            checks_failed=["page_title_evidence_missing"],
        )
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence)
        assert result.code == FailureCode.BROWSER_SESSION_UNAVAILABLE

    def test_unknown_failure_fallback(self, classifier, now):
        match = OutcomeMatch(
            matched=False,
            reason="Unknown issue.",
            checks_failed=["something_unusual"],
        )
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Some Title",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = classifier.classify(match, evidence)
        assert result.code == FailureCode.UNKNOWN_FAILURE

    def test_classify_from_error(self, classifier):
        error = ValueError("Something broke")
        result = classifier.classify_from_error(error, execution_id="e1", step_id="s1")
        assert result.code == FailureCode.UNKNOWN_FAILURE
        assert result.retryable is False
        assert "Something broke" in result.message

    def test_failure_has_execution_and_step_ids(self, classifier, now):
        match = OutcomeMatch(matched=False, reason="Test.", checks_failed=["text_missing"])
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence, execution_id="exec_42", step_id="step_7")
        assert result.execution_id == "exec_42"
        assert result.step_id == "step_7"

    def test_recommended_action_populated(self, classifier, now):
        match = OutcomeMatch(matched=False, reason="Test.", checks_failed=["stale_evidence"])
        evidence = _make_evidence([])
        result = classifier.classify(match, evidence)
        assert result.recommended_action == "REINSPECT"

