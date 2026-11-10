"""Translate a reviewed workflow draft into the executable vocabulary.

Coordinator integration module living in A1's API layer.

The three agent blocks evolved separate vocabularies:

* U2 (learning) describes a demonstrated group in prose: ``action_type`` is an
  inferred intent such as ``"Select 'element'."``, ``target`` carries a
  ``target_summary`` plus page context, and ``expected_result`` uses outcome
  names like ``state_change``.
* U3 (execution) only performs values in ``BrowserExecutor.ALLOWED_ACTIONS``
  with a semantic target (role/name/test_id/label/placeholder/selector).
* U4 (verification) only understands ``OutcomeType`` values such as
  ``navigation``, ``page_title``, ``page_state``, ``element_visible``.

Without translation every drafted step is refused as an unsupported action or
an ambiguous target, and every verification degrades to UNKNOWN, so a
checkpoint could never legitimately advance.

U2 groups several observed events per step and keeps the *group's* first
target summary, which for a click-then-navigate demonstration is the
navigation's placeholder summary rather than the element the user clicked.
This module therefore re-resolves the element identity from the recorded
evidence events themselves — the click event carries
``button name="Next lesson" testid=next-lesson`` — instead of inventing one.

Nothing here fabricates evidence: values used as expectations are the URLs and
titles actually observed during the demonstration. When no specific observable
signal was recorded, the step is raised to HIGH risk so the run pauses for
explicit confirmation instead of acting blindly or passing vacuously.

Ownership: A1 (`backend/app/api/**`), applied as a logged coordinator
integration change. U2/U3/U4 owners should eventually align the vocabulary in
their own modules and delete this bridge.
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas.contracts import BrowserEvent, RiskLevel, WorkflowDraft, WorkflowStep

# U2 outcome type -> U4 OutcomeType
_OUTCOME_ALIASES: dict[str, str] = {
    "page_transition": "navigation",
    "page_title_contains": "page_title",
    "state_change": "page_state",
    "completion_indicator": "page_title",
}

# U4-native outcome types, accepted unchanged.
_U4_OUTCOMES = frozenset(
    {
        "navigation",
        "page_title",
        "element_visible",
        "element_not_visible",
        "text_present",
        "text_changed",
        "element_enabled",
        "element_disabled",
        "page_state",
        "user_confirmation",
    }
)

_NAVIGATION_EVENTS = frozenset({"navigation", "navigate", "page_load"})
_NAME_RE = re.compile(r'name="([^"]*)"')
_TEST_ID_RE = re.compile(r"testid=([^\s\"]+)")


def _parse_target_summary(summary: str) -> tuple[str, str, str]:
    """Split U1's summary (``button name="Next lesson" testid=next-lesson``)."""
    text = (summary or "").strip()
    if not text:
        return "", "", ""
    head = text.split(" ", 1)[0]
    role = head if head and "=" not in head else ""
    if role == "element":
        role = ""
    name_match = _NAME_RE.search(text)
    test_id_match = _TEST_ID_RE.search(text)
    name = name_match.group(1).strip() if name_match else ""
    test_id = test_id_match.group(1).strip() if test_id_match else ""
    if not name and not test_id and role:
        remainder = text[len(head) :].strip()
        if remainder and "=" not in remainder:
            name = remainder
    return role, name, test_id


def _evidence_index(events: list[BrowserEvent] | None) -> dict[str, BrowserEvent]:
    return {event.id: event for event in events} if events else {}


def _evidence_for(step: WorkflowStep, index: dict[str, BrowserEvent]) -> list[BrowserEvent]:
    ids = list((step.target or {}).get("evidence_event_ids") or [])
    return [index[event_id] for event_id in ids if event_id in index]


def _identity_from_evidence(
    evidence: list[BrowserEvent], fallback_summary: str
) -> tuple[str, str, str]:
    """Find the element the user actually acted on among the step's events."""
    for event in evidence:
        role, name, test_id = _parse_target_summary(event.target_summary or "")
        if name or test_id:
            return role, name, test_id
    return _parse_target_summary(fallback_summary)


def _executable_action(target: dict[str, Any], name: str, test_id: str) -> str:
    dominant = str(target.get("dominant_event_type") or "").strip().lower()
    if dominant in _NAVIGATION_EVENTS:
        return "navigate_to_known_page"
    if dominant == "click":
        return "click_semantic_target"
    return "click_semantic_target" if (name or test_id) else "navigate_to_known_page"


def _expected_result(
    expected: dict[str, Any],
    *,
    action_type: str,
    start_url: str,
    landing_url: str,
    page_title: str,
    name: str,
    follow_url: str = "",
) -> dict[str, Any]:
    """Map a U2 expected result onto a U4 OutcomeType with a real signal."""
    outcome_type = str(expected.get("type") or "").strip()
    if outcome_type in _OUTCOME_ALIASES:
        outcome_type = _OUTCOME_ALIASES[outcome_type]

    # A click whose group only contains the pre-click page was nonetheless
    # followed by the next demonstrated page in the same trace. That page
    # transition is the observable outcome of the click.
    if action_type == "click_semantic_target" and follow_url and follow_url != start_url:
        return {
            "type": "navigation",
            "value": follow_url,
            "match_mode": "contains",
            "source_type": expected.get("type"),
        }

    if outcome_type == "navigation":
        url = landing_url or str(expected.get("page_url") or "")
        if url:
            return {
                "type": "navigation",
                "value": url,
                "match_mode": "contains",
                "source_type": expected.get("type"),
            }

    if outcome_type in ("page_title", "page_state"):
        # Never emit an empty expectation: ``"" in anything`` would pass.
        if page_title:
            return {
                "type": "page_title",
                "value": page_title,
                "match_mode": "contains",
                "source_type": expected.get("type"),
            }
        if landing_url:
            return {
                "type": "navigation",
                "value": landing_url,
                "match_mode": "contains",
                "source_type": expected.get("type"),
            }

    if outcome_type == "element_visible" and name:
        return {
            "type": "element_visible",
            "value": name,
            "match_mode": "contains",
            "source_type": expected.get("type"),
        }

    if outcome_type in _U4_OUTCOMES:
        translated = dict(expected)
        translated.setdefault("match_mode", "contains")
        value = translated.get("value")
        if value not in (None, ""):
            return translated

    # Last resort: the demonstrated destination for a navigation step.
    if action_type == "navigate_to_known_page" and landing_url:
        return {
            "type": "navigation",
            "value": landing_url,
            "match_mode": "contains",
            "source_type": expected.get("type"),
        }
    if action_type == "click_semantic_target" and landing_url and landing_url != start_url:
        return {
            "type": "navigation",
            "value": landing_url,
            "match_mode": "contains",
            "source_type": expected.get("type"),
        }
    return {}


def to_executable_step(
    step: WorkflowStep,
    index: dict[str, BrowserEvent] | None = None,
    *,
    follow_url: str = "",
    evidence: list[BrowserEvent] | None = None,
) -> WorkflowStep:
    """Return a step U3 can execute and U4 can verify."""
    target = dict(step.target or {})
    if evidence is None:
        evidence = _evidence_for(step, index or {})
    role, name, test_id = _identity_from_evidence(
        evidence, str(target.get("target_summary") or "")
    )

    start_url = evidence[0].page_url if evidence else str(target.get("page_url") or "")
    landing_url = evidence[-1].page_url if evidence else str(target.get("page_url") or "")
    page_title = next(
        (e.page_title for e in reversed(evidence) if e.page_title), ""
    ) or str(target.get("page_title") or "")

    action_type = _executable_action(target, name, test_id)

    semantic_target: dict[str, Any] = {
        "page_url": landing_url or start_url,
        "page_title": page_title,
        "source_summary": target.get("target_summary") or "",
        "source_group_id": target.get("group_id") or "",
    }
    if role:
        semantic_target["role"] = role
    if name:
        semantic_target["name"] = name
    if test_id:
        semantic_target["test_id"] = test_id
    if action_type == "navigate_to_known_page":
        semantic_target["url"] = landing_url or start_url
        # U3 requires a semantic key; a document role keeps the target
        # meaningful without pretending to know a page element.
        semantic_target.setdefault("role", "document")
        semantic_target.setdefault("name", page_title or landing_url or start_url)
    elif landing_url:
        # Starting point for a click: the page the demonstration began on.
        semantic_target["url"] = start_url

    expected = _expected_result(
        dict(step.expected_result or {}),
        action_type=action_type,
        start_url=start_url,
        landing_url=landing_url,
        page_title=page_title,
        name=name,
        follow_url=follow_url,
    )

    risk = step.risk
    notes: list[str] = []
    if action_type == "click_semantic_target" and not (name or test_id):
        risk = RiskLevel.HIGH
        notes.append(
            f"Step {step.order} has no recorded element identity; "
            "explicit confirmation is required before acting."
        )
    if not expected:
        risk = RiskLevel.HIGH
        notes.append(
            f"Step {step.order} has no verifiable expected outcome; "
            "progress cannot advance without confirmation."
        )
    if notes:
        semantic_target["integration_notes"] = notes

    return step.model_copy(
        update={
            "action_type": action_type,
            "target": semantic_target,
            "expected_result": expected,
            "risk": risk,
        }
    )


def normalize_draft(
    draft: WorkflowDraft, events: list[BrowserEvent] | None = None
) -> WorkflowDraft:
    """Return the draft with every step in the executable vocabulary.

    ``events`` are the captured trace the draft was learned from; they are used
    only to recover the element identity and observed page facts per step.
    """
    index = _evidence_index(events)
    raw_steps = list(draft.steps)
    per_step_evidence = [_evidence_for(step, index) for step in raw_steps]

    steps: list[WorkflowStep] = []
    for position, step in enumerate(raw_steps):
        follow_url = ""
        if position + 1 < len(raw_steps):
            next_evidence = per_step_evidence[position + 1]
            follow_url = next_evidence[0].page_url if next_evidence else ""
            if not follow_url:
                follow_url = str((raw_steps[position + 1].target or {}).get("page_url") or "")
        steps.append(
            to_executable_step(
                step,
                index,
                follow_url=follow_url,
                evidence=per_step_evidence[position],
            )
        )

    return draft.model_copy(update={"steps": steps})


__all__ = ["normalize_draft", "to_executable_step"]
