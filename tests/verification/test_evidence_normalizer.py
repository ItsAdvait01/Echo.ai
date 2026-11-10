"""Tests for the EvidenceNormalizer component."""

from datetime import datetime, timezone, timedelta

import pytest

from app.execution.verifier import (
    EvidenceNormalizer,
    EvidenceType,
    NormalizedEvidence,
)


@pytest.fixture
def normalizer():
    return EvidenceNormalizer(staleness_seconds=60)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


class TestEvidenceNormalizer:
    """Tests for EvidenceNormalizer.normalize()."""

    def test_valid_page_title_evidence(self, normalizer, now):
        ctx = {
            "page_title": "Python - Lesson 3",
            "timestamp": now.isoformat(),
        }
        evidence = normalizer.normalize(ctx, {}, execution_id="e1", step_id="s1", reference_time=now)
        assert isinstance(evidence, NormalizedEvidence)
        titles = evidence.get_by_type(EvidenceType.PAGE_TITLE)
        assert len(titles) == 1
        assert titles[0].value == "Python - Lesson 3"
        assert titles[0].fresh is True
        assert titles[0].execution_id == "e1"
        assert titles[0].step_id == "s1"

    def test_valid_url_evidence(self, normalizer, now):
        ctx = {
            "page_url": "https://example.test/lesson-3",
            "timestamp": now.isoformat(),
        }
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        urls = evidence.get_by_type(EvidenceType.PAGE_URL)
        assert len(urls) == 1
        assert urls[0].value == "https://example.test/lesson-3"
        assert urls[0].fresh is True

    def test_missing_evidence_produces_empty(self, normalizer, now):
        evidence = normalizer.normalize({}, {}, reference_time=now)
        assert len(evidence.items) == 0

    def test_stale_evidence_marked_not_fresh(self, normalizer, now):
        old_time = now - timedelta(seconds=120)
        ctx = {
            "page_title": "Old Page",
            "timestamp": old_time.isoformat(),
        }
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        titles = evidence.get_by_type(EvidenceType.PAGE_TITLE)
        assert len(titles) == 1
        assert titles[0].fresh is False

    def test_conflicting_evidence_multiple_items(self, normalizer, now):
        ctx = {
            "page_url": "https://example.test/page-a",
            "page_title": "Page A",
            "text_content": "Welcome to Page A",
            "timestamp": now.isoformat(),
        }
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        assert len(evidence.items) == 3
        assert evidence.has_fresh_evidence() is True

    def test_invalid_timestamp_uses_fallback(self, normalizer, now):
        ctx = {
            "page_title": "Test",
            "timestamp": "not-a-date",
        }
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        titles = evidence.get_by_type(EvidenceType.PAGE_TITLE)
        assert len(titles) == 1
        assert titles[0].fresh is True  # fallback is now, so fresh

    def test_missing_source_uses_browser_context(self, normalizer, now):
        ctx = {"page_title": "Test", "timestamp": now.isoformat()}
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        assert evidence.items[0].source == "browser_context"

    def test_action_result_evidence(self, normalizer, now):
        action = {"success": True, "timestamp": now.isoformat()}
        evidence = normalizer.normalize({}, action, reference_time=now)
        ar_items = evidence.get_by_type(EvidenceType.ACTION_RESULT)
        assert len(ar_items) == 1
        assert ar_items[0].source == "action_result"
        assert ar_items[0].value["success"] is True

    def test_navigation_evidence(self, normalizer, now):
        ctx = {
            "navigation_completed": True,
            "page_url": "https://example.test/next",
            "timestamp": now.isoformat(),
        }
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        nav = evidence.get_by_type(EvidenceType.NAVIGATION)
        assert len(nav) == 1
        assert nav[0].observation_succeeded is True
        assert nav[0].value == "https://example.test/next"

    def test_visible_elements_evidence(self, normalizer, now):
        ctx = {
            "visible_elements": ["button_next", "title_lesson3"],
            "timestamp": now.isoformat(),
        }
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        vis = evidence.get_by_type(EvidenceType.ELEMENT_VISIBLE)
        assert len(vis) == 2

    def test_page_state_evidence(self, normalizer, now):
        ctx = {
            "page_state": "loaded",
            "timestamp": now.isoformat(),
        }
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        ps = evidence.get_by_type(EvidenceType.PAGE_STATE)
        assert len(ps) == 1
        assert ps[0].value == "loaded"

    def test_element_enabled_disabled_evidence(self, normalizer, now):
        ctx = {
            "element_enabled": "submit_button",
            "element_disabled": "back_button",
            "timestamp": now.isoformat(),
        }
        evidence = normalizer.normalize(ctx, {}, reference_time=now)
        enabled = evidence.get_by_type(EvidenceType.ELEMENT_ENABLED)
        disabled = evidence.get_by_type(EvidenceType.ELEMENT_DISABLED)
        assert len(enabled) == 1
        assert len(disabled) == 1

    def test_has_fresh_evidence_no_items(self, normalizer, now):
        evidence = normalizer.normalize({}, {}, reference_time=now)
        assert evidence.has_fresh_evidence() is False

