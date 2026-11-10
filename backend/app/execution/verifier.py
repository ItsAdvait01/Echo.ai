"""
Checkpoint verification, evidence evaluation, and execution completion validation.

U4 owns this module. It provides independent verification of browser action
outcomes using available evidence, without executing browser actions itself
or directly modifying workflow persistence.

Components implemented here:
    - VerificationStatus, ConfidenceLevel, CheckpointType enums
    - Evidence, EvidenceItem, NormalizedEvidence models
    - ExpectedOutcome, VerificationRequest models
    - VerificationResult, CheckpointResult models
    - FailureDetail model
    - EvidenceNormalizer: converts raw browser context into structured evidence
    - OutcomeEvaluator: compares expected outcomes against observed evidence
    - CheckpointManager: manages checkpoint evaluation and ordering
    - CompletionValidator: validates full workflow execution completion
    - CheckpointVerifier: top-level verification service (entry point for U3)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contracts import WorkflowStep

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class VerificationStatus(StrEnum):
    """Outcome classification for a verification check."""

    VERIFIED = "verified"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ConfidenceLevel(StrEnum):
    """Explainable confidence classification for evidence quality."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class EvidenceType(StrEnum):
    """Types of evidence that can be collected from browser context."""

    PAGE_URL = "page_url"
    PAGE_TITLE = "page_title"
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_NOT_VISIBLE = "element_not_visible"
    TEXT_PRESENT = "text_present"
    TEXT_CHANGED = "text_changed"
    ELEMENT_ENABLED = "element_enabled"
    ELEMENT_DISABLED = "element_disabled"
    NAVIGATION = "navigation"
    PAGE_STATE = "page_state"
    ACTION_RESULT = "action_result"
    USER_CONFIRMATION = "user_confirmation"


class OutcomeType(StrEnum):
    """Supported expected outcome match types."""

    NAVIGATION = "navigation"
    PAGE_TITLE = "page_title"
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_NOT_VISIBLE = "element_not_visible"
    TEXT_PRESENT = "text_present"
    TEXT_CHANGED = "text_changed"
    ELEMENT_ENABLED = "element_enabled"
    ELEMENT_DISABLED = "element_disabled"
    PAGE_STATE = "page_state"
    USER_CONFIRMATION = "user_confirmation"


class CheckpointType(StrEnum):
    """Types of verification checkpoints."""

    PRECONDITION = "precondition"
    POSTCONDITION = "postcondition"
    NAVIGATION = "navigation"
    PAGE_STATE = "page_state"
    USER_APPROVAL = "user_approval"
    EXECUTION_BOUNDARY = "execution_boundary"
    WORKFLOW_COMPLETION = "workflow_completion"


class CheckpointStatus(StrEnum):
    """Status of a checkpoint evaluation."""

    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"
    SKIPPED = "skipped"


class StepProgress(StrEnum):
    """Progression states for a workflow step — only VERIFIED is complete."""

    PLANNED = "planned"
    ATTEMPTED = "attempted"
    EXECUTED = "executed"
    VERIFIED = "verified"


class FailureCode(StrEnum):
    """Categorical failure codes."""

    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    PAGE_STATE_MISMATCH = "PAGE_STATE_MISMATCH"
    EXPECTED_TEXT_MISSING = "EXPECTED_TEXT_MISSING"
    BROWSER_SESSION_UNAVAILABLE = "BROWSER_SESSION_UNAVAILABLE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    USER_CONFIRMATION_REQUIRED = "USER_CONFIRMATION_REQUIRED"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


class WorkflowCompletionStatus(StrEnum):
    """Final workflow execution states."""

    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Evidence models
# ---------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    """A single piece of normalized evidence from the browser context."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:8]}")
    source: str  # e.g. "browser_context", "action_result", "user"
    type: EvidenceType
    value: Any = None
    observed_at: datetime
    execution_id: str = ""
    step_id: str = ""
    fresh: bool = True
    observation_succeeded: bool = True
    source_reference: str | None = None


class NormalizedEvidence(BaseModel):
    """Collection of normalized evidence items for a verification request."""

    model_config = ConfigDict(extra="forbid")

    items: list[EvidenceItem] = Field(default_factory=list)
    execution_id: str = ""
    step_id: str = ""
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_by_type(self, evidence_type: EvidenceType) -> list[EvidenceItem]:
        """Return all evidence items of the given type."""
        return [item for item in self.items if item.type == evidence_type]

    def has_fresh_evidence(self) -> bool:
        """Check whether there is at least one fresh, successful observation."""
        return any(item.fresh and item.observation_succeeded for item in self.items)


# ---------------------------------------------------------------------------
# Expected outcome model
# ---------------------------------------------------------------------------


class ExpectedOutcome(BaseModel):
    """What a workflow step is expected to produce as an observable result."""

    model_config = ConfigDict(extra="forbid")

    type: OutcomeType
    value: Any = None
    match_mode: str = "exact"  # "exact" | "contains" | "starts_with"


# ---------------------------------------------------------------------------
# Verification request
# ---------------------------------------------------------------------------


class VerificationRequest(BaseModel):
    """Structured request from U3 asking U4 to verify an action's outcome."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str
    workflow_id: str = ""
    workflow_version: str = ""
    step_id: str
    action_id: str = ""
    expected_outcome: ExpectedOutcome
    action_result: dict[str, Any] = Field(default_factory=dict)
    browser_context: dict[str, Any] = Field(default_factory=dict)
    checkpoint_requirements: list[dict[str, Any]] = Field(default_factory=list)
    previous_verified_state: dict[str, Any] = Field(default_factory=dict)
    request_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Failure model
# ---------------------------------------------------------------------------


class FailureDetail(BaseModel):
    """Structured failure information."""

    model_config = ConfigDict(extra="forbid")

    code: FailureCode
    category: str = ""
    message: str = ""
    execution_id: str = ""
    step_id: str = ""
    retryable: bool = False
    recommended_action: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Checkpoint models
# ---------------------------------------------------------------------------


class CheckpointDefinition(BaseModel):
    """Definition of a checkpoint in a workflow."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str = Field(default_factory=lambda: f"cp_{uuid.uuid4().hex[:8]}")
    type: CheckpointType
    step_id: str
    order: int = 0
    required: bool = True
    expected_outcome: ExpectedOutcome | None = None
    description: str = ""


class CheckpointResult(BaseModel):
    """Result of evaluating a single checkpoint."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    type: CheckpointType
    status: CheckpointStatus
    step_id: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------


class VerificationResult(BaseModel):
    """Structured verification outcome returned to U3."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str
    step_id: str
    action_id: str = ""
    status: VerificationStatus
    confidence: ConfidenceLevel
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    failure: FailureDetail | None = None
    recovery: dict[str, Any] | None = None
    checkpoints: list[CheckpointResult] = Field(default_factory=list)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def is_success(self) -> bool:
        return self.status == VerificationStatus.VERIFIED

    def allows_continuation(self) -> bool:
        return self.status == VerificationStatus.VERIFIED


# ---------------------------------------------------------------------------
# Workflow completion result
# ---------------------------------------------------------------------------


class WorkflowCompletionResult(BaseModel):
    """Result of validating whether a full workflow execution is complete."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str
    workflow_id: str
    status: WorkflowCompletionStatus
    total_steps: int = 0
    verified_steps: int = 0
    failed_steps: int = 0
    pending_steps: int = 0
    unresolved_checkpoints: list[str] = Field(default_factory=list)
    reason: str = ""
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Component: EvidenceNormalizer
# ---------------------------------------------------------------------------

# Default staleness threshold in seconds (evidence older than this is stale).
_DEFAULT_STALENESS_SECONDS = 60


class EvidenceNormalizer:
    """Convert raw browser context and action results into structured evidence.

    This component does NOT interpret or conclude from evidence.  It only
    normalizes the representation so downstream evaluation is consistent.
    """

    def __init__(self, staleness_seconds: int = _DEFAULT_STALENESS_SECONDS) -> None:
        self._staleness_seconds = staleness_seconds

    def normalize(
        self,
        browser_context: dict[str, Any],
        action_result: dict[str, Any],
        execution_id: str = "",
        step_id: str = "",
        reference_time: datetime | None = None,
    ) -> NormalizedEvidence:
        """Build a ``NormalizedEvidence`` bundle from raw browser context and
        action result dictionaries.

        Parameters
        ----------
        browser_context:
            Observed browser state — keys such as ``page_url``, ``page_title``,
            ``visible_elements``, ``text_content``, ``navigation_completed``,
            ``timestamp``.
        action_result:
            Raw result of the dispatched browser action — keys such as
            ``success``, ``error``, ``timestamp``.
        execution_id:
            The current execution run ID.
        step_id:
            The current step ID within the execution.
        reference_time:
            The wall-clock time to use for freshness comparisons.  Defaults to
            ``utcnow``.
        """

        now = reference_time or datetime.now(timezone.utc)
        items: list[EvidenceItem] = []

        # --- Browser context evidence ---

        ctx_timestamp = self._parse_timestamp(browser_context.get("timestamp"), now)
        is_fresh = self._is_fresh(ctx_timestamp, now)

        if "page_url" in browser_context:
            items.append(
                EvidenceItem(
                    source="browser_context",
                    type=EvidenceType.PAGE_URL,
                    value=browser_context["page_url"],
                    observed_at=ctx_timestamp,
                    execution_id=execution_id,
                    step_id=step_id,
                    fresh=is_fresh,
                )
            )

        if "page_title" in browser_context:
            items.append(
                EvidenceItem(
                    source="browser_context",
                    type=EvidenceType.PAGE_TITLE,
                    value=browser_context["page_title"],
                    observed_at=ctx_timestamp,
                    execution_id=execution_id,
                    step_id=step_id,
                    fresh=is_fresh,
                )
            )

        if "visible_elements" in browser_context:
            for elem in browser_context["visible_elements"]:
                elem_name = elem if isinstance(elem, str) else str(elem)
                items.append(
                    EvidenceItem(
                        source="browser_context",
                        type=EvidenceType.ELEMENT_VISIBLE,
                        value=elem_name,
                        observed_at=ctx_timestamp,
                        execution_id=execution_id,
                        step_id=step_id,
                        fresh=is_fresh,
                    )
                )

        if "text_content" in browser_context:
            items.append(
                EvidenceItem(
                    source="browser_context",
                    type=EvidenceType.TEXT_PRESENT,
                    value=browser_context["text_content"],
                    observed_at=ctx_timestamp,
                    execution_id=execution_id,
                    step_id=step_id,
                    fresh=is_fresh,
                )
            )

        if "navigation_completed" in browser_context:
            items.append(
                EvidenceItem(
                    source="browser_context",
                    type=EvidenceType.NAVIGATION,
                    value=browser_context.get("page_url", ""),
                    observed_at=ctx_timestamp,
                    execution_id=execution_id,
                    step_id=step_id,
                    fresh=is_fresh,
                    observation_succeeded=bool(browser_context["navigation_completed"]),
                )
            )

        if "page_state" in browser_context:
            items.append(
                EvidenceItem(
                    source="browser_context",
                    type=EvidenceType.PAGE_STATE,
                    value=browser_context["page_state"],
                    observed_at=ctx_timestamp,
                    execution_id=execution_id,
                    step_id=step_id,
                    fresh=is_fresh,
                )
            )

        if "element_enabled" in browser_context:
            items.append(
                EvidenceItem(
                    source="browser_context",
                    type=EvidenceType.ELEMENT_ENABLED,
                    value=browser_context["element_enabled"],
                    observed_at=ctx_timestamp,
                    execution_id=execution_id,
                    step_id=step_id,
                    fresh=is_fresh,
                )
            )

        if "element_disabled" in browser_context:
            items.append(
                EvidenceItem(
                    source="browser_context",
                    type=EvidenceType.ELEMENT_DISABLED,
                    value=browser_context["element_disabled"],
                    observed_at=ctx_timestamp,
                    execution_id=execution_id,
                    step_id=step_id,
                    fresh=is_fresh,
                )
            )

        # --- Action result evidence ---

        if action_result:
            ar_timestamp = self._parse_timestamp(action_result.get("timestamp"), now)
            ar_fresh = self._is_fresh(ar_timestamp, now)
            items.append(
                EvidenceItem(
                    source="action_result",
                    type=EvidenceType.ACTION_RESULT,
                    value={
                        "success": action_result.get("success"),
                        "error": action_result.get("error"),
                    },
                    observed_at=ar_timestamp,
                    execution_id=execution_id,
                    step_id=step_id,
                    fresh=ar_fresh,
                    observation_succeeded=bool(action_result.get("success", False)),
                )
            )

        evidence = NormalizedEvidence(
            items=items,
            execution_id=execution_id,
            step_id=step_id,
            collected_at=now,
        )

        logger.info(
            "Evidence normalized: execution=%s step=%s items=%d fresh=%s",
            execution_id,
            step_id,
            len(items),
            evidence.has_fresh_evidence(),
        )

        return evidence

    # --- internal helpers ---

    def _parse_timestamp(
        self, raw: Any, fallback: datetime
    ) -> datetime:
        if raw is None:
            return fallback
        if isinstance(raw, datetime):
            if raw.tzinfo is None:
                return raw.replace(tzinfo=timezone.utc)
            return raw
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                return fallback
        return fallback

    def _is_fresh(self, observed_at: datetime, reference: datetime) -> bool:
        delta = abs((reference - observed_at).total_seconds())
        return delta <= self._staleness_seconds


# ---------------------------------------------------------------------------
# Component: OutcomeEvaluator
# ---------------------------------------------------------------------------


class OutcomeMatch(BaseModel):
    """Result of comparing an expected outcome against observed evidence."""

    model_config = ConfigDict(extra="forbid")

    matched: bool
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)


class OutcomeEvaluator:
    """Compare expected outcomes against normalized evidence.

    Deterministic matching — no LLM required.
    """

    def evaluate(
        self,
        expected: ExpectedOutcome,
        evidence: NormalizedEvidence,
    ) -> OutcomeMatch:
        """Evaluate whether ``expected`` is satisfied by ``evidence``."""

        handler = self._handlers.get(expected.type)
        if handler is None:
            return OutcomeMatch(
                matched=False,
                reason=f"Unsupported outcome type: {expected.type}",
                checks_failed=[f"unsupported_type:{expected.type}"],
            )
        return handler(self, expected, evidence)

    # --- type-specific handlers ---

    def _eval_navigation(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        nav_items = evidence.get_by_type(EvidenceType.NAVIGATION)
        url_items = evidence.get_by_type(EvidenceType.PAGE_URL)

        if not nav_items and not url_items:
            return OutcomeMatch(
                matched=False,
                reason="No navigation or URL evidence available.",
                checks_failed=["navigation_evidence_missing"],
            )

        expected_url = str(expected.value) if expected.value else ""

        # Check navigation completion
        for nav in nav_items:
            if not nav.fresh:
                continue
            if not nav.observation_succeeded:
                return OutcomeMatch(
                    matched=False,
                    reason="Navigation did not complete successfully.",
                    evidence_refs=[nav.evidence_id],
                    checks_failed=["navigation_incomplete"],
                )

        # Check URL match
        for url_ev in url_items:
            if not url_ev.fresh:
                continue
            actual_url = str(url_ev.value) if url_ev.value else ""
            if self._string_match(actual_url, expected_url, expected.match_mode):
                return OutcomeMatch(
                    matched=True,
                    reason=f"Navigation URL matches expected: {expected_url}",
                    evidence_refs=[url_ev.evidence_id],
                    checks_passed=["url_match"],
                )
            else:
                return OutcomeMatch(
                    matched=False,
                    reason=f"URL mismatch: expected '{expected_url}', got '{actual_url}'.",
                    evidence_refs=[url_ev.evidence_id],
                    checks_failed=["url_mismatch"],
                )

        # Only stale evidence available
        return OutcomeMatch(
            matched=False,
            reason="Only stale navigation evidence available.",
            checks_failed=["stale_evidence"],
        )

    def _eval_page_title(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        title_items = evidence.get_by_type(EvidenceType.PAGE_TITLE)
        if not title_items:
            return OutcomeMatch(
                matched=False,
                reason="No page title evidence available.",
                checks_failed=["page_title_evidence_missing"],
            )

        expected_title = str(expected.value) if expected.value else ""
        for item in title_items:
            if not item.fresh:
                continue
            actual_title = str(item.value) if item.value else ""
            if self._string_match(actual_title, expected_title, expected.match_mode):
                return OutcomeMatch(
                    matched=True,
                    reason=f"Page title matches: '{expected_title}'.",
                    evidence_refs=[item.evidence_id],
                    checks_passed=["title_match"],
                )
            else:
                return OutcomeMatch(
                    matched=False,
                    reason=f"Page title mismatch: expected '{expected_title}', got '{actual_title}'.",
                    evidence_refs=[item.evidence_id],
                    checks_failed=["title_mismatch"],
                )

        return OutcomeMatch(
            matched=False,
            reason="Only stale page title evidence available.",
            checks_failed=["stale_evidence"],
        )

    def _eval_element_visible(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        vis_items = evidence.get_by_type(EvidenceType.ELEMENT_VISIBLE)
        if not vis_items:
            return OutcomeMatch(
                matched=False,
                reason="No element visibility evidence available.",
                checks_failed=["element_visible_evidence_missing"],
            )

        expected_elem = str(expected.value) if expected.value else ""
        refs: list[str] = []
        for item in vis_items:
            if not item.fresh:
                continue
            refs.append(item.evidence_id)
            actual = str(item.value) if item.value else ""
            if self._string_match(actual, expected_elem, expected.match_mode):
                return OutcomeMatch(
                    matched=True,
                    reason=f"Expected element '{expected_elem}' is visible.",
                    evidence_refs=[item.evidence_id],
                    checks_passed=["element_visible"],
                )

        if refs:
            return OutcomeMatch(
                matched=False,
                reason=f"Expected element '{expected_elem}' was not found among visible elements.",
                evidence_refs=refs,
                checks_failed=["element_not_found"],
            )

        return OutcomeMatch(
            matched=False,
            reason="Only stale element visibility evidence available.",
            checks_failed=["stale_evidence"],
        )

    def _eval_element_not_visible(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        vis_items = evidence.get_by_type(EvidenceType.ELEMENT_VISIBLE)
        expected_elem = str(expected.value) if expected.value else ""

        # If there are no visible element items at all, that supports the claim
        # that the element is not visible.
        if not vis_items:
            return OutcomeMatch(
                matched=True,
                reason=f"No visible element evidence; '{expected_elem}' is absent.",
                checks_passed=["element_absent_no_evidence"],
            )

        for item in vis_items:
            if not item.fresh:
                continue
            actual = str(item.value) if item.value else ""
            if self._string_match(actual, expected_elem, expected.match_mode):
                return OutcomeMatch(
                    matched=False,
                    reason=f"Element '{expected_elem}' is still visible.",
                    evidence_refs=[item.evidence_id],
                    checks_failed=["element_still_visible"],
                )

        return OutcomeMatch(
            matched=True,
            reason=f"Element '{expected_elem}' is not among visible elements.",
            checks_passed=["element_not_visible"],
        )

    def _eval_text_present(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        text_items = evidence.get_by_type(EvidenceType.TEXT_PRESENT)
        if not text_items:
            return OutcomeMatch(
                matched=False,
                reason="No text content evidence available.",
                checks_failed=["text_evidence_missing"],
            )

        expected_text = str(expected.value) if expected.value else ""
        for item in text_items:
            if not item.fresh:
                continue
            actual_text = str(item.value) if item.value else ""
            # text_present always uses "contains" semantics unless overridden
            mode = expected.match_mode if expected.match_mode != "exact" else "contains"
            if self._string_match(actual_text, expected_text, mode):
                return OutcomeMatch(
                    matched=True,
                    reason=f"Expected text '{expected_text}' found in page content.",
                    evidence_refs=[item.evidence_id],
                    checks_passed=["text_present"],
                )
            else:
                return OutcomeMatch(
                    matched=False,
                    reason=f"Expected text '{expected_text}' not found in page content.",
                    evidence_refs=[item.evidence_id],
                    checks_failed=["text_missing"],
                )

        return OutcomeMatch(
            matched=False,
            reason="Only stale text evidence available.",
            checks_failed=["stale_evidence"],
        )

    def _eval_text_changed(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        text_items = evidence.get_by_type(EvidenceType.TEXT_PRESENT)
        if not text_items:
            return OutcomeMatch(
                matched=False,
                reason="No text evidence to detect change.",
                checks_failed=["text_evidence_missing"],
            )
        # For text_changed, expected.value should be the *old* text.
        old_text = str(expected.value) if expected.value else ""
        for item in text_items:
            if not item.fresh:
                continue
            current_text = str(item.value) if item.value else ""
            if current_text != old_text:
                return OutcomeMatch(
                    matched=True,
                    reason=f"Text changed from '{old_text}' to '{current_text}'.",
                    evidence_refs=[item.evidence_id],
                    checks_passed=["text_changed"],
                )
            else:
                return OutcomeMatch(
                    matched=False,
                    reason="Text has not changed from the expected old value.",
                    evidence_refs=[item.evidence_id],
                    checks_failed=["text_unchanged"],
                )
        return OutcomeMatch(
            matched=False,
            reason="Only stale text evidence available for change detection.",
            checks_failed=["stale_evidence"],
        )

    def _eval_element_enabled(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        items = evidence.get_by_type(EvidenceType.ELEMENT_ENABLED)
        if not items:
            return OutcomeMatch(
                matched=False,
                reason="No element-enabled evidence available.",
                checks_failed=["element_enabled_evidence_missing"],
            )
        expected_elem = str(expected.value) if expected.value else ""
        for item in items:
            if not item.fresh:
                continue
            actual = str(item.value) if item.value else ""
            if self._string_match(actual, expected_elem, expected.match_mode):
                return OutcomeMatch(
                    matched=True,
                    reason=f"Element '{expected_elem}' is enabled.",
                    evidence_refs=[item.evidence_id],
                    checks_passed=["element_enabled"],
                )
        return OutcomeMatch(
            matched=False,
            reason=f"Element '{expected_elem}' is not confirmed as enabled.",
            checks_failed=["element_not_enabled"],
        )

    def _eval_element_disabled(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        items = evidence.get_by_type(EvidenceType.ELEMENT_DISABLED)
        if not items:
            return OutcomeMatch(
                matched=False,
                reason="No element-disabled evidence available.",
                checks_failed=["element_disabled_evidence_missing"],
            )
        expected_elem = str(expected.value) if expected.value else ""
        for item in items:
            if not item.fresh:
                continue
            actual = str(item.value) if item.value else ""
            if self._string_match(actual, expected_elem, expected.match_mode):
                return OutcomeMatch(
                    matched=True,
                    reason=f"Element '{expected_elem}' is disabled.",
                    evidence_refs=[item.evidence_id],
                    checks_passed=["element_disabled"],
                )
        return OutcomeMatch(
            matched=False,
            reason=f"Element '{expected_elem}' is not confirmed as disabled.",
            checks_failed=["element_not_disabled"],
        )

    def _eval_page_state(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        items = evidence.get_by_type(EvidenceType.PAGE_STATE)
        if not items:
            return OutcomeMatch(
                matched=False,
                reason="No page state evidence available.",
                checks_failed=["page_state_evidence_missing"],
            )
        expected_state = str(expected.value) if expected.value else ""
        for item in items:
            if not item.fresh:
                continue
            actual_state = str(item.value) if item.value else ""
            if self._string_match(actual_state, expected_state, expected.match_mode):
                return OutcomeMatch(
                    matched=True,
                    reason=f"Page state matches: '{expected_state}'.",
                    evidence_refs=[item.evidence_id],
                    checks_passed=["page_state_match"],
                )
            else:
                return OutcomeMatch(
                    matched=False,
                    reason=f"Page state mismatch: expected '{expected_state}', "
                    f"got '{actual_state}'.",
                    evidence_refs=[item.evidence_id],
                    checks_failed=["page_state_mismatch"],
                )
        return OutcomeMatch(
            matched=False,
            reason="Only stale page state evidence available.",
            checks_failed=["stale_evidence"],
        )

    def _eval_user_confirmation(
        self, expected: ExpectedOutcome, evidence: NormalizedEvidence
    ) -> OutcomeMatch:
        items = evidence.get_by_type(EvidenceType.USER_CONFIRMATION)
        if not items:
            return OutcomeMatch(
                matched=False,
                reason="No user confirmation evidence available.",
                checks_failed=["user_confirmation_missing"],
            )
        for item in items:
            if item.observation_succeeded:
                return OutcomeMatch(
                    matched=True,
                    reason="User confirmed the expected outcome.",
                    evidence_refs=[item.evidence_id],
                    checks_passed=["user_confirmed"],
                )
        return OutcomeMatch(
            matched=False,
            reason="User did not confirm the expected outcome.",
            checks_failed=["user_not_confirmed"],
        )

    # Handler dispatch table
    _handlers: dict[OutcomeType, Any] = {
        OutcomeType.NAVIGATION: _eval_navigation,
        OutcomeType.PAGE_TITLE: _eval_page_title,
        OutcomeType.ELEMENT_VISIBLE: _eval_element_visible,
        OutcomeType.ELEMENT_NOT_VISIBLE: _eval_element_not_visible,
        OutcomeType.TEXT_PRESENT: _eval_text_present,
        OutcomeType.TEXT_CHANGED: _eval_text_changed,
        OutcomeType.ELEMENT_ENABLED: _eval_element_enabled,
        OutcomeType.ELEMENT_DISABLED: _eval_element_disabled,
        OutcomeType.PAGE_STATE: _eval_page_state,
        OutcomeType.USER_CONFIRMATION: _eval_user_confirmation,
    }

    # --- string matching helper ---

    @staticmethod
    def _string_match(actual: str, expected: str, mode: str) -> bool:
        if mode == "exact":
            return actual == expected
        if mode == "contains":
            return expected in actual
        if mode == "starts_with":
            return actual.startswith(expected)
        return actual == expected


# ---------------------------------------------------------------------------
# Component: CheckpointManager
# ---------------------------------------------------------------------------


class CheckpointManager:
    """Evaluate workflow checkpoints and enforce ordering constraints.

    Checkpoints add meaningful reliability at critical transitions —
    they are not inserted after every trivial interaction.
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, CheckpointDefinition] = {}
        self._results: dict[str, CheckpointResult] = {}

    def register(self, checkpoint: CheckpointDefinition) -> None:
        """Register a checkpoint definition."""
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint

    def evaluate(
        self,
        checkpoint_id: str,
        evidence: NormalizedEvidence,
        evaluator: OutcomeEvaluator,
    ) -> CheckpointResult:
        """Evaluate a single checkpoint against evidence."""

        cp = self._checkpoints.get(checkpoint_id)
        if cp is None:
            result = CheckpointResult(
                checkpoint_id=checkpoint_id,
                type=CheckpointType.POSTCONDITION,
                status=CheckpointStatus.FAILED,
                reason=f"Checkpoint '{checkpoint_id}' is not registered.",
            )
            self._results[checkpoint_id] = result
            return result

        # User-approval checkpoints need explicit user confirmation evidence
        if cp.type == CheckpointType.USER_APPROVAL:
            confirm_items = evidence.get_by_type(EvidenceType.USER_CONFIRMATION)
            if any(item.observation_succeeded for item in confirm_items):
                result = CheckpointResult(
                    checkpoint_id=checkpoint_id,
                    type=cp.type,
                    status=CheckpointStatus.PASSED,
                    step_id=cp.step_id,
                    reason="User approval received.",
                    evidence_refs=[i.evidence_id for i in confirm_items],
                )
            else:
                result = CheckpointResult(
                    checkpoint_id=checkpoint_id,
                    type=cp.type,
                    status=CheckpointStatus.PENDING,
                    step_id=cp.step_id,
                    reason="Waiting for user approval.",
                )
            self._results[checkpoint_id] = result
            return result

        # For checkpoints with expected outcomes, evaluate against evidence
        if cp.expected_outcome is not None:
            match = evaluator.evaluate(cp.expected_outcome, evidence)
            if match.matched:
                status = CheckpointStatus.PASSED
            else:
                status = CheckpointStatus.FAILED
            result = CheckpointResult(
                checkpoint_id=checkpoint_id,
                type=cp.type,
                status=status,
                step_id=cp.step_id,
                evidence_refs=match.evidence_refs,
                reason=match.reason,
            )
        else:
            # A checkpoint without an expected outcome passes if any fresh
            # evidence is available — it only gates on "we observed something".
            if evidence.has_fresh_evidence():
                result = CheckpointResult(
                    checkpoint_id=checkpoint_id,
                    type=cp.type,
                    status=CheckpointStatus.PASSED,
                    step_id=cp.step_id,
                    reason="Fresh evidence available; checkpoint passes.",
                )
            else:
                result = CheckpointResult(
                    checkpoint_id=checkpoint_id,
                    type=cp.type,
                    status=CheckpointStatus.FAILED,
                    step_id=cp.step_id,
                    reason="No fresh evidence; checkpoint cannot pass.",
                )

        self._results[checkpoint_id] = result
        return result

    def get_result(self, checkpoint_id: str) -> CheckpointResult | None:
        return self._results.get(checkpoint_id)

    def all_required_passed(self) -> bool:
        """Check whether all registered *required* checkpoints have passed."""
        for cp in self._checkpoints.values():
            if not cp.required:
                continue
            result = self._results.get(cp.checkpoint_id)
            if result is None or result.status != CheckpointStatus.PASSED:
                return False
        return True

    def pending_checkpoints(self) -> list[str]:
        """Return IDs of checkpoints that are pending or unevaluated."""
        pending: list[str] = []
        for cp_id, cp in self._checkpoints.items():
            result = self._results.get(cp_id)
            if result is None or result.status == CheckpointStatus.PENDING:
                pending.append(cp_id)
        return pending

    def failed_required_checkpoints(self) -> list[str]:
        """Return IDs of required checkpoints that have failed."""
        failed: list[str] = []
        for cp_id, cp in self._checkpoints.items():
            if not cp.required:
                continue
            result = self._results.get(cp_id)
            if result is not None and result.status == CheckpointStatus.FAILED:
                failed.append(cp_id)
        return failed

    def can_proceed_past(self, step_id: str) -> bool:
        """Check whether execution can proceed past the given step.

        Returns False if any required checkpoint for this step has failed or
        is still pending.
        """
        for cp in self._checkpoints.values():
            if cp.step_id != step_id or not cp.required:
                continue
            result = self._results.get(cp.checkpoint_id)
            if result is None or result.status != CheckpointStatus.PASSED:
                return False
        return True


# ---------------------------------------------------------------------------
# Component: FailureClassifier
# ---------------------------------------------------------------------------


class FailureClassifier:
    """Classify verification failures into structured categories."""

    def classify(
        self,
        match: OutcomeMatch,
        evidence: NormalizedEvidence,
        execution_id: str = "",
        step_id: str = "",
    ) -> FailureDetail:
        """Produce a :class:`FailureDetail` from a failed outcome match."""

        code = self._infer_code(match, evidence)
        retryable = self._is_retryable(code)
        recommended = self._recommended_action(code)

        return FailureDetail(
            code=code,
            category=self._category_for(code),
            message=match.reason,
            execution_id=execution_id,
            step_id=step_id,
            retryable=retryable,
            recommended_action=recommended,
            evidence_refs=match.evidence_refs,
        )

    def classify_from_error(
        self,
        error: Exception,
        execution_id: str = "",
        step_id: str = "",
    ) -> FailureDetail:
        """Classify a raw exception into a structured failure."""
        return FailureDetail(
            code=FailureCode.UNKNOWN_FAILURE,
            category="internal",
            message=str(error),
            execution_id=execution_id,
            step_id=step_id,
            retryable=False,
            recommended_action="PAUSE",
        )

    # --- internal helpers ---

    def _infer_code(
        self, match: OutcomeMatch, evidence: NormalizedEvidence
    ) -> FailureCode:
        failed_checks = set(match.checks_failed)

        if "stale_evidence" in failed_checks:
            return FailureCode.STALE_EVIDENCE
        if "element_not_found" in failed_checks:
            return FailureCode.ELEMENT_NOT_FOUND
        if "navigation_incomplete" in failed_checks or "url_mismatch" in failed_checks:
            return FailureCode.NAVIGATION_FAILED
        if "page_state_mismatch" in failed_checks:
            return FailureCode.PAGE_STATE_MISMATCH
        if "text_missing" in failed_checks:
            return FailureCode.EXPECTED_TEXT_MISSING
        if "user_confirmation_missing" in failed_checks or "user_not_confirmed" in failed_checks:
            return FailureCode.USER_CONFIRMATION_REQUIRED

        # Check for evidence conflicts
        for etype in EvidenceType:
            items = evidence.get_by_type(etype)
            values = {str(i.value) for i in items if i.fresh}
            if len(values) > 1:
                return FailureCode.EVIDENCE_CONFLICT

        # Evidence missing entirely
        if not evidence.items:
            return FailureCode.BROWSER_SESSION_UNAVAILABLE

        for check in failed_checks:
            if "evidence_missing" in check:
                return FailureCode.BROWSER_SESSION_UNAVAILABLE

        return FailureCode.UNKNOWN_FAILURE

    def _is_retryable(self, code: FailureCode) -> bool:
        retryable_codes = {
            FailureCode.NAVIGATION_FAILED,
            FailureCode.ELEMENT_NOT_FOUND,
            FailureCode.ACTION_TIMEOUT,
            FailureCode.STALE_EVIDENCE,
        }
        return code in retryable_codes

    def _recommended_action(self, code: FailureCode) -> str:
        action_map: dict[FailureCode, str] = {
            FailureCode.ELEMENT_NOT_FOUND: "REINSPECT",
            FailureCode.NAVIGATION_FAILED: "RETRY_SAFE",
            FailureCode.PAGE_STATE_MISMATCH: "REINSPECT",
            FailureCode.EXPECTED_TEXT_MISSING: "REINSPECT",
            FailureCode.BROWSER_SESSION_UNAVAILABLE: "PAUSE",
            FailureCode.STALE_EVIDENCE: "REINSPECT",
            FailureCode.EVIDENCE_CONFLICT: "PAUSE",
            FailureCode.PRECONDITION_FAILED: "REPLAN",
            FailureCode.POSTCONDITION_FAILED: "REINSPECT",
            FailureCode.ACTION_TIMEOUT: "RETRY_SAFE",
            FailureCode.PERMISSION_REQUIRED: "ASK_USER",
            FailureCode.USER_CONFIRMATION_REQUIRED: "ASK_USER",
            FailureCode.UNSUPPORTED_OPERATION: "ABORT",
            FailureCode.UNKNOWN_FAILURE: "PAUSE",
        }
        return action_map.get(code, "PAUSE")

    @staticmethod
    def _category_for(code: FailureCode) -> str:
        categories: dict[FailureCode, str] = {
            FailureCode.ELEMENT_NOT_FOUND: "element",
            FailureCode.NAVIGATION_FAILED: "navigation",
            FailureCode.PAGE_STATE_MISMATCH: "state",
            FailureCode.EXPECTED_TEXT_MISSING: "content",
            FailureCode.BROWSER_SESSION_UNAVAILABLE: "session",
            FailureCode.STALE_EVIDENCE: "evidence",
            FailureCode.EVIDENCE_CONFLICT: "evidence",
            FailureCode.PRECONDITION_FAILED: "precondition",
            FailureCode.POSTCONDITION_FAILED: "postcondition",
            FailureCode.ACTION_TIMEOUT: "timeout",
            FailureCode.PERMISSION_REQUIRED: "permission",
            FailureCode.USER_CONFIRMATION_REQUIRED: "confirmation",
            FailureCode.UNSUPPORTED_OPERATION: "unsupported",
            FailureCode.UNKNOWN_FAILURE: "unknown",
        }
        return categories.get(code, "unknown")


# ---------------------------------------------------------------------------
# Component: CompletionValidator
# ---------------------------------------------------------------------------


class CompletionValidator:
    """Validate whether a full workflow execution can be marked complete.

    A workflow must NOT be reported as completed merely because U3 dispatched
    its last planned action.
    """

    def validate(
        self,
        execution_id: str,
        workflow_id: str,
        step_results: dict[str, VerificationResult],
        total_steps: int,
        checkpoint_manager: CheckpointManager | None = None,
    ) -> WorkflowCompletionResult:
        """Evaluate full workflow completion.

        Parameters
        ----------
        step_results:
            Mapping of step_id -> VerificationResult for every step that was
            submitted for verification.
        total_steps:
            The total number of steps in the workflow.
        checkpoint_manager:
            If provided, required checkpoints are also validated.
        """
        verified = 0
        failed = 0
        blocked = 0
        needs_review = 0

        for result in step_results.values():
            if result.status == VerificationStatus.VERIFIED:
                verified += 1
            elif result.status == VerificationStatus.FAILED:
                failed += 1
            elif result.status == VerificationStatus.BLOCKED:
                blocked += 1
            elif result.status == VerificationStatus.NEEDS_REVIEW:
                needs_review += 1

        pending = total_steps - len(step_results)
        unresolved_cps: list[str] = []

        if checkpoint_manager is not None:
            unresolved_cps = (
                checkpoint_manager.pending_checkpoints()
                + checkpoint_manager.failed_required_checkpoints()
            )

        # Determine completion status
        if blocked > 0:
            status = WorkflowCompletionStatus.BLOCKED
            reason = f"{blocked} step(s) blocked; cannot complete."
        elif needs_review > 0:
            status = WorkflowCompletionStatus.NEEDS_REVIEW
            reason = f"{needs_review} step(s) need review."
        elif failed > 0:
            status = WorkflowCompletionStatus.FAILED
            reason = f"{failed} step(s) failed."
        elif unresolved_cps:
            status = WorkflowCompletionStatus.NEEDS_REVIEW
            reason = f"{len(unresolved_cps)} checkpoint(s) unresolved."
        elif pending > 0:
            status = WorkflowCompletionStatus.PARTIALLY_COMPLETED
            reason = f"{pending} step(s) not yet verified."
        elif verified == total_steps and total_steps > 0:
            status = WorkflowCompletionStatus.COMPLETED
            reason = f"All {total_steps} step(s) verified."
        else:
            status = WorkflowCompletionStatus.NEEDS_REVIEW
            reason = "Completion status could not be determined."

        return WorkflowCompletionResult(
            execution_id=execution_id,
            workflow_id=workflow_id,
            status=status,
            total_steps=total_steps,
            verified_steps=verified,
            failed_steps=failed,
            pending_steps=pending,
            unresolved_checkpoints=unresolved_cps,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Component: ConfidenceAssessor
# ---------------------------------------------------------------------------


class ConfidenceAssessor:
    """Assess the confidence level of a verification based on evidence quality.

    This is explainable — not a magic number.
    """

    def assess(
        self,
        evidence: NormalizedEvidence,
        match: OutcomeMatch,
    ) -> ConfidenceLevel:
        """Return a confidence level based on evidence quality and match."""

        if not evidence.items:
            return ConfidenceLevel.INSUFFICIENT

        fresh_items = [i for i in evidence.items if i.fresh and i.observation_succeeded]
        if not fresh_items:
            return ConfidenceLevel.INSUFFICIENT

        # Count evidence sources
        sources = {i.source for i in fresh_items}
        evidence_types = {i.type for i in fresh_items}

        if not match.matched:
            # Even for failures, we can be confident if evidence is strong
            if len(fresh_items) >= 2 and len(sources) >= 2:
                return ConfidenceLevel.HIGH
            if fresh_items:
                return ConfidenceLevel.MEDIUM
            return ConfidenceLevel.INSUFFICIENT

        # For matches: more diverse evidence = higher confidence
        if len(fresh_items) >= 2 and len(evidence_types) >= 2:
            return ConfidenceLevel.HIGH
        if len(fresh_items) >= 1:
            # Single source — check if it's an action_result alone (not sufficient)
            if evidence_types == {EvidenceType.ACTION_RESULT}:
                return ConfidenceLevel.LOW
            return ConfidenceLevel.MEDIUM

        return ConfidenceLevel.INSUFFICIENT


# ---------------------------------------------------------------------------
# Main service: CheckpointVerifier — the entry point for U3
# ---------------------------------------------------------------------------


class CheckpointVerifier:
    """U4's top-level verification service.

    Provides:
    - ``verify``: verify a single step from ``WorkflowStep.expected_result``
    - ``verify_request``: verify from a full ``VerificationRequest``
    - ``verify_checkpoint``: evaluate a registered checkpoint
    - ``verify_completion``: validate full workflow completion
    - ``classify_failure``: classify a failure from error/context
    """

    def __init__(
        self,
        staleness_seconds: int = _DEFAULT_STALENESS_SECONDS,
    ) -> None:
        self._normalizer = EvidenceNormalizer(staleness_seconds=staleness_seconds)
        self._evaluator = OutcomeEvaluator()
        self._failure_classifier = FailureClassifier()
        self._confidence_assessor = ConfidenceAssessor()
        self._checkpoint_manager = CheckpointManager()
        self._step_results: dict[str, VerificationResult] = {}
        self._seen_keys: set[str] = set()

    @property
    def checkpoint_manager(self) -> CheckpointManager:
        return self._checkpoint_manager

    # -----------------------------------------------------------------------
    # Primary interface — consumed by U3
    # -----------------------------------------------------------------------

    def verify(
        self, step: WorkflowStep, observed_state: dict[str, Any]
    ) -> dict[str, Any]:
        """Verify a step using its ``expected_result`` and observed browser state.

        This is the simple interface matching the original starter stub.
        Returns a typed pass/fail outcome; never advances progress itself.

        Parameters
        ----------
        step:
            The workflow step containing ``expected_result``.
        observed_state:
            Raw browser context dictionary from U1/U3.
        """

        # Build expected outcome from step.expected_result
        expected = self._build_expected_outcome(step.expected_result)
        if expected is None:
            logger.warning(
                "Step %s has no verifiable expected_result; marking UNKNOWN.",
                step.id,
            )
            return VerificationResult(
                execution_id="",
                step_id=step.id,
                status=VerificationStatus.UNKNOWN,
                confidence=ConfidenceLevel.INSUFFICIENT,
                reason="Step has no verifiable expected outcome defined.",
            ).model_dump()

        # Normalize evidence
        evidence = self._normalizer.normalize(
            browser_context=observed_state,
            action_result=observed_state.get("action_result", {}),
            step_id=step.id,
        )

        # Evaluate outcome
        match = self._evaluator.evaluate(expected, evidence)

        # Assess confidence
        confidence = self._confidence_assessor.assess(evidence, match)

        # Determine status
        status = self._determine_status(match, evidence, confidence)

        # Classify failure if not verified
        failure: FailureDetail | None = None
        recovery: dict[str, Any] | None = None
        if status != VerificationStatus.VERIFIED:
            failure = self._failure_classifier.classify(
                match, evidence, step_id=step.id
            )
            recovery = {
                "action": failure.recommended_action,
                "requires_user": failure.recommended_action in ("ASK_USER", "ABORT"),
            }

        result = VerificationResult(
            execution_id="",
            step_id=step.id,
            status=status,
            confidence=confidence,
            reason=match.reason,
            evidence_refs=match.evidence_refs,
            failure=failure,
            recovery=recovery,
        )

        logger.info(
            "Verification: step=%s status=%s confidence=%s reason=%s",
            step.id,
            status,
            confidence,
            match.reason,
        )

        return result.model_dump()

    def verify_request(self, request: VerificationRequest) -> VerificationResult:
        """Full structured verification from a ``VerificationRequest``.

        Uses idempotency key (execution_id + step_id + action_id) to prevent
        duplicate contradictory outcomes.
        """

        idempotency_key = f"{request.execution_id}:{request.step_id}:{request.action_id}"

        # Check for duplicate
        if idempotency_key in self._seen_keys:
            existing = self._step_results.get(request.step_id)
            if existing is not None:
                logger.info(
                    "Duplicate verification request: %s — returning cached result.",
                    idempotency_key,
                )
                return existing

        self._seen_keys.add(idempotency_key)

        logger.info(
            "Verification request received: execution=%s step=%s action=%s",
            request.execution_id,
            request.step_id,
            request.action_id,
        )

        # Normalize evidence
        evidence = self._normalizer.normalize(
            browser_context=request.browser_context,
            action_result=request.action_result,
            execution_id=request.execution_id,
            step_id=request.step_id,
        )

        # Evaluate expected outcome
        match = self._evaluator.evaluate(request.expected_outcome, evidence)

        # Assess confidence
        confidence = self._confidence_assessor.assess(evidence, match)

        # Determine status
        status = self._determine_status(match, evidence, confidence)

        # Evaluate any checkpoint requirements
        checkpoint_results: list[CheckpointResult] = []
        for cp_spec in request.checkpoint_requirements:
            cp_id = cp_spec.get("checkpoint_id", "")
            if cp_id and cp_id in self._checkpoint_manager._checkpoints:
                cp_result = self._checkpoint_manager.evaluate(
                    cp_id, evidence, self._evaluator
                )
                checkpoint_results.append(cp_result)
                if cp_result.status == CheckpointStatus.FAILED:
                    # A required checkpoint failure blocks continuation
                    cp_def = self._checkpoint_manager._checkpoints.get(cp_id)
                    if cp_def and cp_def.required:
                        status = VerificationStatus.BLOCKED

        # Classify failure
        failure: FailureDetail | None = None
        recovery: dict[str, Any] | None = None
        if status != VerificationStatus.VERIFIED:
            failure = self._failure_classifier.classify(
                match,
                evidence,
                execution_id=request.execution_id,
                step_id=request.step_id,
            )
            recovery = {
                "action": failure.recommended_action,
                "requires_user": failure.recommended_action in ("ASK_USER", "ABORT"),
            }

        result = VerificationResult(
            execution_id=request.execution_id,
            step_id=request.step_id,
            action_id=request.action_id,
            status=status,
            confidence=confidence,
            reason=match.reason,
            evidence_refs=match.evidence_refs,
            failure=failure,
            recovery=recovery,
            checkpoints=checkpoint_results,
        )

        # Cache result
        self._step_results[request.step_id] = result

        logger.info(
            "Verification result: execution=%s step=%s status=%s confidence=%s",
            request.execution_id,
            request.step_id,
            status,
            confidence,
        )

        return result

    def verify_checkpoint(
        self,
        checkpoint_id: str,
        browser_context: dict[str, Any],
        execution_id: str = "",
        step_id: str = "",
    ) -> CheckpointResult:
        """Evaluate a single registered checkpoint."""
        evidence = self._normalizer.normalize(
            browser_context=browser_context,
            action_result={},
            execution_id=execution_id,
            step_id=step_id,
        )
        return self._checkpoint_manager.evaluate(
            checkpoint_id, evidence, self._evaluator
        )

    def verify_completion(
        self,
        execution_id: str,
        workflow_id: str,
        total_steps: int,
    ) -> WorkflowCompletionResult:
        """Validate whether the full workflow execution is complete."""
        return CompletionValidator().validate(
            execution_id=execution_id,
            workflow_id=workflow_id,
            step_results=self._step_results,
            total_steps=total_steps,
            checkpoint_manager=self._checkpoint_manager,
        )

    def classify_failure(
        self,
        error: Exception,
        execution_id: str = "",
        step_id: str = "",
    ) -> FailureDetail:
        """Classify a raw exception into a structured failure."""
        return self._failure_classifier.classify_from_error(
            error, execution_id=execution_id, step_id=step_id
        )

    def register_checkpoint(self, checkpoint: CheckpointDefinition) -> None:
        """Register a checkpoint for later evaluation."""
        self._checkpoint_manager.register(checkpoint)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _determine_status(
        self,
        match: OutcomeMatch,
        evidence: NormalizedEvidence,
        confidence: ConfidenceLevel,
    ) -> VerificationStatus:
        """Apply deterministic rules to select verification status."""

        # No evidence at all → UNKNOWN
        if not evidence.items:
            return VerificationStatus.UNKNOWN

        # No fresh evidence → UNKNOWN (stale data cannot confirm anything)
        if not evidence.has_fresh_evidence():
            return VerificationStatus.UNKNOWN

        # Action result alone (no independent outcome evidence) → NEEDS_REVIEW
        # A click success alone is NOT proof of the expected outcome.
        fresh_non_action = [
            i
            for i in evidence.items
            if i.fresh and i.observation_succeeded and i.type != EvidenceType.ACTION_RESULT
        ]
        if not fresh_non_action:
            return VerificationStatus.NEEDS_REVIEW

        if match.matched:
            if confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM):
                return VerificationStatus.VERIFIED
            if confidence == ConfidenceLevel.LOW:
                return VerificationStatus.NEEDS_REVIEW
            return VerificationStatus.UNKNOWN

        # Not matched
        if confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM):
            return VerificationStatus.FAILED
        if confidence == ConfidenceLevel.LOW:
            return VerificationStatus.NEEDS_REVIEW

        return VerificationStatus.UNKNOWN

    @staticmethod
    def _build_expected_outcome(
        expected_result: dict[str, Any],
    ) -> ExpectedOutcome | None:
        """Convert a ``WorkflowStep.expected_result`` dict into an
        ``ExpectedOutcome`` model.  Returns ``None`` if the dict is empty
        or un-parseable.
        """
        if not expected_result:
            return None

        outcome_type = expected_result.get("type")
        if outcome_type is None:
            return None

        try:
            otype = OutcomeType(outcome_type)
        except ValueError:
            return None

        return ExpectedOutcome(
            type=otype,
            value=expected_result.get("value"),
            match_mode=expected_result.get("match_mode", "exact"),
        )
