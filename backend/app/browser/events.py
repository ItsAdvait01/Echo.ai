"""Normalized browser event model. U1 owns this module.

``ObservedEvent`` is U1's internal, fully normalized record. It carries the
extra evidence (page id, sequence number, semantic target descriptor, metadata)
that U2 needs, while ``to_contract()`` projects it onto the shared
``app.schemas.contracts.BrowserEvent`` owned by A1.

The shared contract is the published interface; this model never replaces it.
Wider fields are proposed to A1 as an additive change request (see
``backend/app/browser/README.md``), not implemented unilaterally.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contracts import BrowserEvent

OBSERVER_SOURCE = "playwright_observer"


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp used for every event."""
    return datetime.now(UTC)


class EventType(StrEnum):
    """Event types the observer can emit.

    Deliberately small: every type maps to one observable browser fact and none
    of them encode an interpretation of user intent (that is U2's job).
    """

    NAVIGATION = "navigation"
    PAGE_LOAD = "page_load"
    CLICK = "click"
    TEXT_INPUT = "text_input"
    SELECT_CHANGE = "select_change"
    FORM_SUBMIT = "form_submit"
    KEY_PRESS = "key_press"
    SCROLL = "scroll"
    PAGE_OPEN = "page_open"
    PAGE_CLOSE = "page_close"
    PAGE_CRASH = "page_crash"


class TargetDescriptor(BaseModel):
    """Semantic description of the element an interaction targeted.

    Evidence, not a replay guarantee: it prefers accessibility information and
    stable test ids over fragile positional selectors.
    """

    model_config = ConfigDict(extra="forbid")

    tag: str = ""
    role: str | None = None
    accessible_name: str | None = None
    visible_text: str | None = None
    test_id: str | None = None
    locator_hint: str | None = None
    field_category: str | None = None

    def summary(self) -> str:
        """Compact human/U2 readable description, e.g. ``button name="Next lesson"``."""
        parts: list[str] = [self.tag or "element"]
        if self.role and self.role != self.tag:
            parts.append(f"role={self.role}")
        if self.accessible_name:
            parts.append(f'name="{self.accessible_name}"')
        if self.test_id:
            parts.append(f"testid={self.test_id}")
        if self.field_category:
            parts.append(f"field={self.field_category}")
        text = " ".join(parts)
        return text[:200]


class PageState(BaseModel):
    """Lightweight page context captured around an event."""

    model_config = ConfigDict(extra="forbid")

    url: str = ""
    title: str = ""
    url_redacted: bool = False


class ObservedEvent(BaseModel):
    """Normalized, sanitized, ordered browser event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    page_id: str
    sequence_number: int = Field(ge=1)
    event_type: EventType
    timestamp: datetime = Field(default_factory=utc_now)
    source: str = OBSERVER_SOURCE
    target: TargetDescriptor = Field(default_factory=TargetDescriptor)
    page_state: PageState = Field(default_factory=PageState)
    metadata: dict[str, Any] = Field(default_factory=dict)
    safe_value: str | None = None
    sensitive_data_removed: bool = False

    def to_contract(self) -> BrowserEvent:
        """Project onto the shared A1 ``BrowserEvent`` contract.

        ``safe_value`` is only ever a sanitized select label; typed values are
        never stored anywhere in this pipeline.
        """
        return BrowserEvent(
            id=self.event_id,
            session_id=self.session_id,
            timestamp=self.timestamp,
            event_type=str(self.event_type),
            page_url=self.page_state.url,
            page_title=self.page_state.title,
            target_summary=self.target.summary() or None,
            safe_value=self.safe_value,
        )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "OBSERVER_SOURCE",
    "EventType",
    "ObservedEvent",
    "PageState",
    "TargetDescriptor",
    "utc_now",
]
