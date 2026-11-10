"""U2 workflow learner test suite.

Covers the complete event-to-draft pipeline and the 20 required scenarios
using local fixtures and synthetic BrowserEvent objects. Run from ``backend/``:

    python -m pytest tests/ -v
"""

from datetime import datetime, timezone

import pytest

from app.schemas.contracts import BrowserEvent, WorkflowDraft, WorkflowStep
from app.learning import (
    WorkflowLearner,
    WorkflowLearnerService,
    WorkflowDraftError,
    EventProcessor,
    EventGrouper,
    IntentInference,
    StepExtractor,
    DraftValidator,
    RuleBasedFallback,
    SUPPORTED,
    UNCERTAIN,
    INCOMPLETE,
)
from app.learning.llm import LLMProvider


BASE = datetime(2026, 11, 5, 20, 0, 0, tzinfo=timezone.utc)


def _event(eid, sid, etype, url, title, target, ts=None):
    return BrowserEvent(
        id=eid,
        session_id=sid,
        timestamp=ts or BASE,
        event_type=etype,
        page_url=url,
        page_title=title,
        target_summary=target,
    )


def _demo_events():
    """User: open platform -> open Python course -> select lesson -> next lesson."""
    return [
        _event(
            "e1", "s1", "navigation", "https://learn.example.com/", "Home", "Platform home", BASE
        ),
        _event(
            "e2",
            "s1",
            "click",
            "https://learn.example.com/",
            "Home",
            "Python Course",
            BASE.replace(minute=20, second=1),
        ),
        _event(
            "e3",
            "s1",
            "navigation",
            "https://learn.example.com/course/python",
            "Python Course",
            "Python course page",
            BASE.replace(minute=20, second=5),
        ),
        _event(
            "e4",
            "s1",
            "click",
            "https://learn.example.com/course/python",
            "Python Course",
            "Lesson 1: Intro",
            BASE.replace(minute=20, second=10),
        ),
        _event(
            "e5",
            "s1",
            "navigation",
            "https://learn.example.com/course/python/lesson1",
            "Lesson 1",
            "Lesson 1 page",
            BASE.replace(minute=20, second=15),
        ),
        _event(
            "e6",
            "s1",
            "click",
            "https://learn.example.com/course/python/lesson1",
            "Lesson 1",
            "Next lesson",
            BASE.replace(minute=20, second=20),
        ),
        _event(
            "e7",
            "s1",
            "navigation",
            "https://learn.example.com/course/python/lesson2",
            "Lesson 2",
            "Lesson 2 page",
            BASE.replace(minute=20, second=25),
        ),
    ]


# --------------------------------------------------------------------------- #
# 1. Valid event ingestion
# --------------------------------------------------------------------------- #
def test_valid_event_ingestion_returns_draft():
    learner = WorkflowLearner()
    draft = learner.create_draft(_demo_events())
    assert isinstance(draft, WorkflowDraft)
    assert draft.name
    assert draft.description
    assert len(draft.steps) >= 1
    assert draft.trigger_phrases


# --------------------------------------------------------------------------- #
# 2. Invalid event rejection
# --------------------------------------------------------------------------- #
def test_invalid_event_rejection():
    bad = _event("e1", "s1", "click", "", "Home", "Python Course", BASE)  # empty page_url
    with pytest.raises(WorkflowDraftError) as exc:
        WorkflowLearner().create_draft([bad])
    assert exc.value.code == "malformed_events"


def test_non_browser_event_rejection():
    with pytest.raises(WorkflowDraftError):
        WorkflowLearner().create_draft([{"not": "an event"}])


# --------------------------------------------------------------------------- #
# 3. Event ordering
# --------------------------------------------------------------------------- #
def test_event_ordering_preserved_by_timestamp():
    events = [
        _event(
            "e3",
            "s1",
            "navigation",
            "https://learn.example.com/course/python",
            "Python Course",
            "page",
            BASE.replace(minute=20, second=5),
        ),
        _event("e1", "s1", "navigation", "https://learn.example.com/", "Home", "home", BASE),
        _event(
            "e2",
            "s1",
            "click",
            "https://learn.example.com/",
            "Home",
            "Python Course",
            BASE.replace(minute=20, second=1),
        ),
    ]
    processor = EventProcessor()
    normalized = processor.process(events)
    ids = [e.id for e in normalized.events]
    assert ids == ["e1", "e2", "e3"]
    assert normalized.ordering == "by_timestamp"


# --------------------------------------------------------------------------- #
# 4. Duplicate event detection
# --------------------------------------------------------------------------- #
def test_duplicate_event_detection():
    events = [
        _event("e1", "s1", "navigation", "https://learn.example.com/", "Home", "home", BASE),
        _event(
            "e1",
            "s1",
            "click",
            "https://learn.example.com/",
            "Home",
            "Python Course",
            BASE.replace(minute=20, second=1),
        ),
    ]
    with pytest.raises(WorkflowDraftError) as exc:
        WorkflowLearner().create_draft(events)
    assert exc.value.code == "duplicate_events"
    assert exc.value.details.get("duplicate_event_ids") == ["e1"]


# --------------------------------------------------------------------------- #
# 5. Multiple session isolation
# --------------------------------------------------------------------------- #
def test_multiple_session_isolation_rejected():
    events = [
        _event("e1", "s1", "navigation", "https://learn.example.com/", "Home", "home", BASE),
        _event(
            "e2",
            "s2",
            "click",
            "https://learn.example.com/",
            "Home",
            "Python Course",
            BASE.replace(minute=20, second=1),
        ),
    ]
    with pytest.raises(WorkflowDraftError) as exc:
        WorkflowLearner().create_draft(events)
    assert exc.value.code == "mixed_sessions"
    assert "s1" in exc.value.details["session_ids"]
    assert "s2" in exc.value.details["session_ids"]


# --------------------------------------------------------------------------- #
# 6. Grouping related browser events
# --------------------------------------------------------------------------- #
def test_grouping_related_browser_events():
    grouper = EventGrouper()
    groups = grouper.group(_demo_events())
    # Same-page navigation+click should collapse into fewer groups than events.
    assert len(groups) < len(_demo_events())
    assert all(g.event_ids for g in groups)
    # Groups preserve order.
    flat = [eid for g in groups for eid in g.event_ids]
    assert flat == [e.id for e in _demo_events()]


# --------------------------------------------------------------------------- #
# 7. Filtering irrelevant noise
# --------------------------------------------------------------------------- #
def test_filtering_irrelevant_noise():
    events = [
        _event("e1", "s1", "navigation", "https://learn.example.com/", "Home", "home", BASE),
        _event(
            "e2",
            "s1",
            "scroll",
            "https://learn.example.com/",
            "Home",
            "scroll",
            BASE.replace(minute=20, second=1),
        ),
        _event(
            "e3",
            "s1",
            "scroll",
            "https://learn.example.com/",
            "Home",
            "scroll",
            BASE.replace(minute=20, second=2),
        ),
        _event(
            "e4",
            "s1",
            "focus",
            "https://learn.example.com/",
            "Home",
            "input focus",
            BASE.replace(minute=20, second=3),
        ),
        _event(
            "e5",
            "s1",
            "click",
            "https://learn.example.com/",
            "Home",
            "Python Course",
            BASE.replace(minute=20, second=4),
        ),
    ]
    grouper = EventGrouper()
    groups = grouper.group(events)
    assert len(groups) == 1
    # Only the navigation and the click survive; repeated scrolls and focus are dropped.
    assert groups[0].dominant_type == "click"


# --------------------------------------------------------------------------- #
# 8. Preserving meaningful events
# --------------------------------------------------------------------------- #
def test_preserving_meaningful_events():
    events = [
        _event("e1", "s1", "click", "https://learn.example.com/", "Home", "Course A", BASE),
        _event(
            "e2",
            "s1",
            "navigation",
            "https://learn.example.com/course/a",
            "Course A",
            "page",
            BASE.replace(minute=20, second=1),
        ),
        _event(
            "e3",
            "s1",
            "click",
            "https://learn.example.com/course/a",
            "Course A",
            "Lesson 1",
            BASE.replace(minute=20, second=2),
        ),
        _event(
            "e4",
            "s1",
            "navigation",
            "https://learn.example.com/course/a/lesson1",
            "Lesson 1",
            "page",
            BASE.replace(minute=20, second=3),
        ),
        # Same target clicked again later is meaningful (re-attempt), preserved.
        _event(
            "e5",
            "s1",
            "click",
            "https://learn.example.com/course/a/lesson1",
            "Lesson 1",
            "Lesson 1",
            BASE.replace(minute=20, second=4),
        ),
    ]
    grouper = EventGrouper()
    groups = grouper.group(events)
    # At least two groups (course page + lesson page) because URLs differ.
    assert len(groups) >= 2


# --------------------------------------------------------------------------- #
# 9. Inferring a simple navigation task
# --------------------------------------------------------------------------- #
def test_inferring_simple_navigation_task():
    inferrer = IntentInference()
    grouper = EventGrouper()
    groups = grouper.group(_demo_events())
    inference = inferrer.infer_workflow(groups)
    assert inference["intent"] != "unclear"
    assert inference["status"] in {SUPPORTED, UNCERTAIN}
    assert inference["confidence"] >= 0
    assert inference["reasoning"]
    assert inference["trigger_phrases"]


# --------------------------------------------------------------------------- #
# 10. Extracting logical workflow steps
# --------------------------------------------------------------------------- #
def test_extracting_logical_workflow_steps():
    extractor = StepExtractor()
    inferrer = IntentInference()
    grouper = EventGrouper()
    groups = grouper.group(_demo_events())
    inference = inferrer.infer_workflow(groups)
    steps = extractor.extract(groups, inference)
    assert len(steps) == len(groups)
    for i, step in enumerate(steps, start=1):
        assert step["id"] == f"step-{i}"
        assert step["order"] == i
        assert step["action_type"]
        assert isinstance(step["target"], dict)
        assert isinstance(step["expected_result"], dict)
        assert step["risk"] in {"low", "high"}


# --------------------------------------------------------------------------- #
# 11. Attaching supporting evidence IDs
# --------------------------------------------------------------------------- #
def test_attaching_supporting_evidence_ids():
    extractor = StepExtractor()
    inferrer = IntentInference()
    grouper = EventGrouper()
    groups = grouper.group(_demo_events())
    inference = inferrer.infer_workflow(groups)
    steps = extractor.extract(groups, inference)
    all_event_ids = {e.id for e in _demo_events()}
    for step in steps:
        evidence = step["target"]["evidence_event_ids"]
        assert evidence, f"step {step['order']} missing evidence"
        assert set(evidence) <= all_event_ids


# --------------------------------------------------------------------------- #
# 12. Confidence assignment
# --------------------------------------------------------------------------- #
def test_confidence_assignment():
    learner = WorkflowLearner()
    draft = learner.create_draft(_demo_events())
    for step in draft.steps:
        conf = step.target.get("confidence")
        status = step.target.get("inference_status")
        assert 0.0 <= conf <= 1.0
        assert status in {SUPPORTED, UNCERTAIN, INCOMPLETE}


# --------------------------------------------------------------------------- #
# 13. Uncertain intent handling
# --------------------------------------------------------------------------- #
def test_uncertain_intent_handling():
    # A single click with no page transition and no clear target -> uncertain.
    events = [
        _event("e1", "s1", "click", "https://learn.example.com/", "Home", "Something vague", BASE),
    ]
    learner = WorkflowLearner()
    draft = learner.create_draft(events)
    # Should still produce a draft (not crash), flagged uncertain.
    uncertain_steps = [
        s
        for s in draft.steps
        if s.target.get("inference_status") in {UNCERTAIN, INCOMPLETE}
        or s.target.get("requires_confirmation")
    ]
    assert uncertain_steps, "ambiguous step should be flagged uncertain/confirmation"
    for s in uncertain_steps:
        assert s.risk == "high"
    assert draft.uncertainty_notes


# --------------------------------------------------------------------------- #
# 14. Empty demonstration handling
# --------------------------------------------------------------------------- #
def test_empty_demonstration_handling():
    with pytest.raises(WorkflowDraftError) as exc:
        WorkflowLearner().create_draft([])
    assert exc.value.code == "empty_stream"
    with pytest.raises(WorkflowDraftError):
        WorkflowLearner().create_draft(None)


# --------------------------------------------------------------------------- #
# 15. Malformed LLM response handling
# --------------------------------------------------------------------------- #
class _BadLLM(LLMProvider):
    def generate_workflow_proposal(self, *, session_id, events):
        return {"not": "a valid proposal"}


class _ExplodingLLM(LLMProvider):
    def generate_workflow_proposal(self, *, session_id, events):
        raise RuntimeError("llm down")


def test_malformed_llm_response_falls_back_to_rules():
    service = WorkflowLearnerService(llm_provider=_BadLLM())
    draft = service.generate_workflow_draft("s1", _demo_events())
    assert isinstance(draft, WorkflowDraft)
    assert len(draft.steps) >= 1


def test_llm_timeout_falls_back_to_rules():
    service = WorkflowLearnerService(llm_provider=_ExplodingLLM())
    draft = service.generate_workflow_draft("s1", _demo_events())
    assert isinstance(draft, WorkflowDraft)
    assert len(draft.steps) >= 1


def test_draft_validator_rejects_bad_shape():
    validator = DraftValidator()
    bad = WorkflowDraft(name="", description="", trigger_phrases=[], steps=[], uncertainty_notes=[])
    result = validator.validate(bad)
    assert result.valid is False
    assert result.errors


# --------------------------------------------------------------------------- #
# 16. Rule-based fallback
# --------------------------------------------------------------------------- #
def test_rule_based_fallback_produces_proposal():
    fallback = RuleBasedFallback()
    proposal = fallback.generate_workflow_proposal(session_id="s1", events=_demo_events())
    assert "name" in proposal and proposal["name"]
    assert "description" in proposal
    assert isinstance(proposal["steps"], list) and len(proposal["steps"]) >= 1
    assert "uncertainty_notes" in proposal


# --------------------------------------------------------------------------- #
# 17. Workflow schema validation
# --------------------------------------------------------------------------- #
def test_workflow_schema_validation():
    learner = WorkflowLearner()
    draft = learner.create_draft(_demo_events())
    result = DraftValidator().validate(draft)
    assert result.valid is True, result.errors
    # Every step round-trips through the Pydantic WorkflowStep contract.
    for step in draft.steps:
        dumped = step.model_dump()
        WorkflowStep(**dumped)


# --------------------------------------------------------------------------- #
# 18. Draft status enforcement
# --------------------------------------------------------------------------- #
def test_draft_status_enforcement():
    service = WorkflowLearnerService()
    draft = service.generate_workflow_draft("s1", _demo_events())
    validation = service.validate_workflow_draft(draft)
    assert validation["valid"] is True
    assert validation["status"] == "draft"
    assert validation["never_auto_approved"] is True


# --------------------------------------------------------------------------- #
# 19. No automatic workflow approval
# --------------------------------------------------------------------------- #
def test_no_automatic_workflow_approval():
    learner = WorkflowLearner()
    draft = learner.create_draft(_demo_events())
    # create_draft returns a WorkflowDraft, never an approved Workflow.
    assert isinstance(draft, WorkflowDraft)
    service = WorkflowLearnerService()
    handoff = service.submit_workflow_draft(draft, session_id="s1")
    assert handoff["approved"] is False
    assert handoff["status"] == "draft"
    assert handoff["requires_user_approval"] is True


# --------------------------------------------------------------------------- #
# 20. A2 handoff contract
# --------------------------------------------------------------------------- #
def test_a2_handoff_contract():
    service = WorkflowLearnerService()
    draft = service.generate_workflow_draft("s1", _demo_events())
    handoff = service.submit_workflow_draft(draft, session_id="s1")
    assert handoff["handoff_ready"] is True
    assert handoff["workflow_id"]
    assert handoff["session_id"] == "s1"
    assert handoff["name"]
    assert handoff["description"]
    assert handoff["version"] == 1
    assert handoff["created_at"]
    assert isinstance(handoff["steps"], list) and len(handoff["steps"]) >= 1
    assert isinstance(handoff["uncertainty_notes"], list)
    # Handoff steps carry evidence references and confidence metadata.
    for step in handoff["steps"]:
        assert "evidence_event_ids" in step["target"]
        assert "confidence" in step["target"]
        assert "inference_status" in step["target"]


# --------------------------------------------------------------------------- #
# Demo scenario: full Teach -> draft
# --------------------------------------------------------------------------- #
def test_demo_scenario_python_course_navigation():
    """User opens a learning platform, opens a Python course, selects a lesson,
    navigates to the next lesson. Expect a structured draft with evidence and
    explicit draft status."""
    learner = WorkflowLearner()
    draft = learner.create_draft(_demo_events())

    assert isinstance(draft, WorkflowDraft)
    assert draft.name == "Continue my Python course"
    assert "Python" in draft.description or "course" in draft.description.lower()
    assert any("Python" in p or "course" in p.lower() for p in draft.trigger_phrases)

    # Ordered steps describing the navigation sequence.
    orders = [s.order for s in draft.steps]
    assert orders == list(range(1, len(draft.steps) + 1))

    # Every step traces back to observed events.
    for step in draft.steps:
        assert step.target["evidence_event_ids"]
        assert 0.0 <= step.target["confidence"] <= 1.0

    # Draft status is explicit; nothing is approved.
    handoff = WorkflowLearnerService().submit_workflow_draft(draft, session_id="s1")
    assert handoff["status"] == "draft"
    assert handoff["approved"] is False
