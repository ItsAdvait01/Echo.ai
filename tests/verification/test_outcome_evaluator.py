"""Tests for the OutcomeEvaluator component."""

from datetime import datetime, timezone

import pytest

from app.execution.verifier import (
    EvidenceItem,
    EvidenceType,
    ExpectedOutcome,
    NormalizedEvidence,
    OutcomeEvaluator,
    OutcomeType,
)


@pytest.fixture
def evaluator():
    return OutcomeEvaluator()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _make_evidence(items: list[EvidenceItem]) -> NormalizedEvidence:
    return NormalizedEvidence(items=items)


class TestOutcomeEvaluator:

    def test_expected_title_matches(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Python - Lesson 3")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Python - Lesson 3",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True
        assert "title_match" in result.checks_passed

    def test_expected_title_differs(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Python - Lesson 3")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Python - Lesson 2",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False
        assert "title_mismatch" in result.checks_failed

    def test_expected_element_visible(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.ELEMENT_VISIBLE, value="next_button")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.ELEMENT_VISIBLE,
                value="next_button",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_expected_element_absent(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.ELEMENT_VISIBLE, value="missing_button")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.ELEMENT_VISIBLE,
                value="other_button",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False

    def test_expected_text_present(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.TEXT_PRESENT, value="Welcome")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.TEXT_PRESENT,
                value="Welcome to the course",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_expected_navigation_completed(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.NAVIGATION, value="https://example.test/lesson-3")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.NAVIGATION,
                value="https://example.test/lesson-3",
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
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_navigation_url_mismatch(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.NAVIGATION, value="https://example.test/lesson-3")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.NAVIGATION,
                value="https://example.test/lesson-2",
                observed_at=now,
                fresh=True,
                observation_succeeded=True,
            ),
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_URL,
                value="https://example.test/lesson-2",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False

    def test_evidence_missing_returns_not_matched(self, evaluator):
        expected = ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="Anything")
        evidence = _make_evidence([])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False
        assert "page_title_evidence_missing" in result.checks_failed

    def test_conflicting_stale_evidence(self, evaluator, now):
        from datetime import timedelta
        old = now - timedelta(seconds=120)
        expected = ExpectedOutcome(type=OutcomeType.PAGE_TITLE, value="New Title")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Old Title",
                observed_at=old,
                fresh=False,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False
        assert "stale_evidence" in result.checks_failed

    def test_element_not_visible(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.ELEMENT_NOT_VISIBLE, value="popup_dialog")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.ELEMENT_VISIBLE,
                value="main_content",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_element_not_visible_but_still_present(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.ELEMENT_NOT_VISIBLE, value="popup_dialog")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.ELEMENT_VISIBLE,
                value="popup_dialog",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False

    def test_text_changed(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.TEXT_CHANGED, value="Old text")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.TEXT_PRESENT,
                value="New text",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_text_not_changed(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.TEXT_CHANGED, value="Same text")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.TEXT_PRESENT,
                value="Same text",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False

    def test_page_state_match(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.PAGE_STATE, value="loaded")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_STATE,
                value="loaded",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_page_state_mismatch(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.PAGE_STATE, value="loaded")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_STATE,
                value="loading",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False

    def test_user_confirmation_success(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.USER_CONFIRMATION, value=True)
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
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_user_confirmation_not_given(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.USER_CONFIRMATION, value=True)
        evidence = _make_evidence([
            EvidenceItem(
                source="user",
                type=EvidenceType.USER_CONFIRMATION,
                value=False,
                observed_at=now,
                fresh=True,
                observation_succeeded=False,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is False

    def test_contains_match_mode(self, evaluator, now):
        expected = ExpectedOutcome(
            type=OutcomeType.PAGE_TITLE,
            value="Lesson",
            match_mode="contains",
        )
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Python - Lesson 3 - Introduction",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_starts_with_match_mode(self, evaluator, now):
        expected = ExpectedOutcome(
            type=OutcomeType.PAGE_TITLE,
            value="Python",
            match_mode="starts_with",
        )
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.PAGE_TITLE,
                value="Python - Lesson 3",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_element_enabled(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.ELEMENT_ENABLED, value="submit_btn")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.ELEMENT_ENABLED,
                value="submit_btn",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

    def test_element_disabled(self, evaluator, now):
        expected = ExpectedOutcome(type=OutcomeType.ELEMENT_DISABLED, value="delete_btn")
        evidence = _make_evidence([
            EvidenceItem(
                source="browser_context",
                type=EvidenceType.ELEMENT_DISABLED,
                value="delete_btn",
                observed_at=now,
                fresh=True,
            ),
        ])
        result = evaluator.evaluate(expected, evidence)
        assert result.matched is True

