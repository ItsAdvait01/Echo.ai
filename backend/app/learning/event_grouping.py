"""Event grouping and noise reduction.

Groups a normalized event stream into logical activities using page transitions,
target descriptors, visible labels and navigation outcomes. Irrelevant noise is
filtered where safe; meaningful repeated events are preserved.
"""

from app.schemas.contracts import BrowserEvent

# Event types treated as DOM/lifecycle noise when not accompanied by a target.
NOISE_TYPES = {"focus", "blur", "resize", "domloaded", "domcontentloaded", "load"}
# Navigation-like types that signal a page transition.
NAVIGATION_TYPES = {"navigation", "navigate", "page_load", "url_change"}
# Scroll events are candidates for noise reduction.
SCROLL_TYPES = {"scroll"}


class EventGroup:
    """A cluster of events representing one logical activity."""

    def __init__(
        self,
        group_id: str,
        *,
        events: list[BrowserEvent],
        primary_page_url: str,
        primary_page_title: str,
        dominant_type: str,
        target_summaries: list[str],
        primary_target: str | None,
        has_page_transition: bool,
    ) -> None:
        self.group_id = group_id
        self.events = events
        self.event_ids = [e.id for e in events]
        self.primary_page_url = primary_page_url
        self.primary_page_title = primary_page_title
        self.dominant_type = dominant_type
        self.target_summaries = target_summaries
        self.primary_target = primary_target
        self.has_page_transition = has_page_transition
        self.primary_target = target_summaries[0] if target_summaries else None


class EventGrouper:
    """Groups validated events into workflow activities."""

    NOISE_TYPES = NOISE_TYPES
    NAVIGATION_TYPES = NAVIGATION_TYPES
    SCROLL_TYPES = SCROLL_TYPES

    def group(self, events: list[BrowserEvent]) -> list[EventGroup]:
        """Return ordered groups of related events.

        Ordering is preserved. Events are split at page transitions; consecutive
        noise events on the same page are collapsed; duplicate consecutive
        navigations are collapsed.
        """
        if not events:
            return []

        groups: list[EventGroup] = []
        current: list[BrowserEvent] = []
        current_url: str | None = None
        group_index = 0

        def flush() -> None:
            nonlocal current, group_index
            if not current:
                return
            filtered = self._filter_noise(current)
            if filtered:
                groups.append(self._build_group(group_index, filtered))
                group_index += 1
            current = []

        for event in events:
            url_changed = current_url is not None and event.page_url != current_url
            if url_changed and current:
                flush()
                current_url = event.page_url
            elif current_url is None:
                current_url = event.page_url
            current.append(event)

        flush()
        return groups

    def _filter_noise(self, events: list[BrowserEvent]) -> list[BrowserEvent]:
        """Remove safe noise while preserving meaningful events."""
        filtered: list[BrowserEvent] = []
        prev: BrowserEvent | None = None
        for event in events:
            etype = (event.event_type or "").lower()

            # Drop incidental lifecycle/focus noise outright.
            if etype in self.NOISE_TYPES:
                prev = event
                continue

            # Collapse repeated scrolls on the same page to the first one.
            if etype in self.SCROLL_TYPES:
                if prev is not None and (
                    (prev.event_type or "").lower() in self.SCROLL_TYPES
                    and prev.page_url == event.page_url
                ):
                    continue
                filtered.append(event)
                prev = event
                continue

            # Collapse duplicate consecutive navigations to the same page.
            if etype in self.NAVIGATION_TYPES:
                if (
                    prev is not None
                    and (prev.event_type or "").lower() in self.NAVIGATION_TYPES
                    and prev.page_url == event.page_url
                    and prev.page_title == event.page_title
                ):
                    continue

            filtered.append(event)
            prev = event
        return filtered

    def _build_group(self, index: int, events: list[BrowserEvent]) -> EventGroup:
        types = [(e.event_type or "").lower() for e in events]
        dominant = types[0] if types else "unknown"
        # Prefer a click/action type as dominant when present.
        for t in types:
            if t in {"click", "submit", "change", "select"}:
                dominant = t
                break
        target_summaries = [e.target_summary or "" for e in events if e.target_summary]
        # Prefer a target from an action event (click/submit/select/change) so
        # the meaningful control, not a navigation's label, leads the group.
        action_targets = [
            e.target_summary
            for e in events
            if e.target_summary
            and (e.event_type or "").lower() in {"click", "submit", "select", "change"}
        ]
        primary_target = (
            action_targets[0]
            if action_targets
            else (target_summaries[0] if target_summaries else None)
        )
        # A group has a page transition when its events span more than one URL.
        urls = {e.page_url for e in events}
        return EventGroup(
            group_id=f"group-{index + 1}",
            events=events,
            primary_page_url=events[0].page_url,
            primary_page_title=events[0].page_title,
            dominant_type=dominant,
            target_summaries=target_summaries,
            primary_target=primary_target,
            has_page_transition=len(urls) > 1,
        )
