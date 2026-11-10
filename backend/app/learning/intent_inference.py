"""Intent inference for the U2 workflow learner.

Distinguishes observed facts from inferred intent. Each inference carries
supporting event ids, a coarse confidence, a status, a reasoning summary and any
unresolved ambiguity. A click alone is weak evidence; page transitions and
changed page state strengthen it.
"""

from typing import Any

from app.learning.confidence import ConfidenceModel, SUPPORTED, UNCERTAIN
from app.learning.event_grouping import EventGroup


class IntentInference:
    """Infer the likely purpose of an event group and of the whole stream."""

    def __init__(self) -> None:
        self.confidence = ConfidenceModel()

    def infer_group(self, group: EventGroup, position: int = 0) -> dict[str, Any]:
        """Infer intent for a single grouped activity.

        ``position`` is the 0-based index of the group in the stream, used to
        distinguish the first arrival from later steps.
        """
        event_ids = group.event_ids
        dominant = group.dominant_type
        target = group.primary_target or ""
        has_transition = group.has_page_transition
        page_title = group.primary_page_title or ""
        page_url = group.primary_page_url or ""

        status, intent, reasoning, ambiguity = self._classify(
            dominant,
            target,
            has_transition,
            page_title,
            page_url,
            position=position,
        )

        confidence = self.confidence.score(
            status,
            evidence_count=len(event_ids),
            has_page_transition=has_transition,
            has_confirmation=self._looks_like_confirmation(page_title, target),
        )

        return {
            "group_id": group.group_id,
            "intent": intent,
            "status": status,
            "confidence": confidence,
            "reasoning": reasoning,
            "ambiguity": ambiguity,
            "supporting_event_ids": list(event_ids),
            "page_url": page_url,
            "page_title": page_title,
            "target_summary": target,
            "dominant_event_type": dominant,
        }

    def infer_workflow(self, groups: list[EventGroup]) -> dict[str, Any]:
        """Infer an overall workflow intent and trigger phrases from all groups."""
        if not groups:
            return {
                "intent": "unclear",
                "status": "incomplete",
                "confidence": 0.0,
                "reasoning": "No event groups were produced; the demonstration "
                "contained insufficient evidence to infer a task.",
                "ambiguity": "Cannot determine the user's goal.",
                "trigger_phrases": [],
                "supporting_event_ids": [],
            }

        group_inferences = [self.infer_group(g, position=idx) for idx, g in enumerate(groups)]

        # Overall status is the weakest non-direct link.
        rank = {
            "directly_observed": 3,
            "supported": 2,
            "uncertain": 1,
            "incomplete": 0,
        }
        worst = min(group_inferences, key=lambda i: rank.get(i["status"], 0))

        intents = [g["intent"] for g in group_inferences if g["intent"] != "unclear"]
        if intents:
            intent = "; ".join(dict.fromkeys(intents))
        else:
            intent = "unclear"

        trigger_phrases = self._suggest_triggers(intent, groups)

        return {
            "intent": intent,
            "status": worst["status"],
            "confidence": worst["confidence"],
            "reasoning": (
                "Overall task inferred from "
                f"{len(group_inferences)} observed activity group(s). " + worst["reasoning"]
            ),
            "ambiguity": worst["ambiguity"],
            "trigger_phrases": trigger_phrases,
            "supporting_event_ids": [
                eid for g in group_inferences for eid in g["supporting_event_ids"]
            ],
            "group_inferences": group_inferences,
        }

    def _classify(
        self,
        dominant: str,
        target: str,
        has_transition: bool,
        page_title: str,
        page_url: str,
        *,
        position: int = 0,
    ) -> tuple[str, str, str, str]:
        """Classify one group into intent/status/reasoning/ambiguity."""
        target_l = (target or "").lower()
        title_l = (page_title or "").lower()
        # Signals come from visible text and URL path segments, never the host,
        # so a domain like "learn.example.com" does not false-positive as a course.
        visible = f"{title_l} {target_l}"
        path = self._url_path(page_url)

        course_words = {"course", "python", "lesson", "learn"}
        completion_words = {"complete", "completed", "done", "finished", "success"}
        home_words = {"home", "dashboard", "platform"}

        course_signal = any(w in course_words for w in visible.split()) or any(
            w in course_words for w in path
        )
        completion_signal = any(w in completion_words for w in visible.split()) or any(
            w in completion_words for w in path
        )
        next_signal = any(w in target_l for w in ("next lesson", "continue", "next"))
        home_signal = position == 0 and (
            any(w in home_words for w in visible.split())
            or any(w in home_words for w in path)
            or path == []
        )

        # First arrival at the platform/home page.
        if home_signal and dominant in {
            "navigation",
            "navigate",
            "page_load",
            "url_change",
        }:
            return (
                SUPPORTED,
                "Open the learning platform.",
                f"Page opened at {page_title or page_url}.",
                "The purpose of opening the platform is not stated by the events alone.",
            )

        # Completion indicator: a page or control that signals done/complete.
        if dominant in {"click", "submit"} and completion_signal:
            return (
                SUPPORTED,
                "Mark the current item complete.",
                (
                    f"User clicked '{target or 'a control'}' and the resulting page "
                    "shows a completion/final state."
                ),
                "The click and resulting state support completion, but the exact "
                "control semantics are not machine-confirmed.",
            )

        # Move to the next lesson / content item.
        if next_signal:
            status = SUPPORTED if has_transition else UNCERTAIN
            return (
                status,
                "Move to the next lesson or content item.",
                (
                    f"User activated '{target or 'a next control'}' and the page "
                    + ("advanced to a new page." if has_transition else "")
                ),
                (
                    ""
                    if has_transition
                    else "No page transition observed; the next target is not "
                    "confirmed to have loaded."
                ),
            )

        # Open a course or content item.
        if course_signal and dominant in {
            "click",
            "navigation",
            "navigate",
            "page_load",
        }:
            return (
                SUPPORTED,
                f"Open {target or 'the course'}.",
                (
                    f"User interacted with '{target or 'a course target'}' on a "
                    f"course-related page ({page_title or page_url})."
                ),
                "The specific lesson or item targeted is inferred from labels only.",
            )

        # Select a lesson or named item.
        if dominant in {"click", "submit"}:
            return (
                UNCERTAIN,
                f"Select '{target or 'a control'}'.",
                f"User clicked '{target or 'a control'}'; no clear outcome observed.",
                "No page transition or confirmation observed; effect is unknown.",
            )

        # Generic page navigation.
        if dominant in {"navigation", "navigate", "page_load", "url_change"}:
            return (
                SUPPORTED,
                "Navigate to a page.",
                f"Page changed to {page_title or page_url}.",
                "The purpose of the navigation is not stated by the events alone.",
            )

        return (
            UNCERTAIN,
            "unclear",
            f"Observed {dominant} on {page_title or page_url} without a clear target.",
            "Insufficient evidence to infer intent.",
        )

    @staticmethod
    def _looks_like_confirmation(page_title: str, target: str) -> bool:
        text = f"{page_title} {target}".lower()
        return any(w in text for w in ("complete", "completed", "done", "success"))

    @staticmethod
    def _url_path(page_url: str) -> list[str]:
        """Return lowercased URL path segments (host excluded)."""
        try:
            from urllib.parse import urlparse

            path = urlparse(page_url or "").path
        except Exception:
            path = page_url or ""
        return [seg for seg in path.strip("/").split("/") if seg]

    @staticmethod
    def _suggest_triggers(intent: str, groups: list[EventGroup]) -> list[str]:
        phrases: list[str] = []
        if "course" in intent.lower() or "python" in intent.lower():
            phrases.extend(
                [
                    "Continue my Python course",
                    "Resume Python lessons",
                    "Continue my course",
                ]
            )
        elif "next lesson" in intent.lower():
            phrases.extend(["Continue to the next lesson", "Resume my lesson", "Next lesson"])
        else:
            phrases.append("Continue the demonstrated task")
        seen: set[str] = set()
        for g in groups:
            for label in (g.primary_page_title, g.primary_target):
                if label and label not in seen:
                    seen.add(label)
                    phrases.append(f"Continue {label}")
        return list(dict.fromkeys(phrases))[:5]
