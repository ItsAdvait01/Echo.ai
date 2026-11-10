"""Privacy sanitization for observed browser data. U1 owns this module.

This module is the single place where page-provided data is turned into
retainable data. Everything the observer emits passes through it, because page
content is untrusted input: a hostile page can call the exposed binding with
arbitrary payloads.

Rules enforced here:

* URLs keep scheme, host, port and path only - query strings, fragments and
  userinfo are dropped, and token-shaped path segments are redacted.
* Visible text is whitespace-collapsed, control-character stripped and clipped.
* Credential / payment / authentication fields are recognised so their values
  are never captured and no event is emitted for them.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 512
MAX_TEXT_LENGTH = 80
MAX_TITLE_LENGTH = 200
MAX_LOCATOR_LENGTH = 160
MAX_TEST_ID_LENGTH = 64
MAX_TAG_LENGTH = 32

# Field hints that mean "never retain anything from this control".
_SENSITIVE_HINT = re.compile(
    r"(?<![A-Za-z])("
    r"pass(word|wd|phrase)?|pwd|secret|token|api[-_]?key|auth(orization)?|bearer|"
    r"otp|one[-_]?time|pin|cvv|cvc|csc|card(num(ber)?)?|credit|iban|routing|"
    r"account[-_]?number|security[-_]?code|ssn|social[-_]?security|seed[-_]?phrase|"
    r"session[-_]?id|recovery[-_]?code"
    r")(?![A-Za-z])",
    re.IGNORECASE,
)

SENSITIVE_AUTOCOMPLETE = frozenset(
    {
        "current-password",
        "new-password",
        "one-time-code",
        "cc-number",
        "cc-csc",
        "cc-exp",
        "cc-exp-month",
        "cc-exp-year",
        "cc-name",
        "cc-given-name",
        "cc-family-name",
        "cc-type",
    }
)

SENSITIVE_INPUT_TYPES = frozenset({"password"})

# Long opaque segments inside a path are treated as tokens, not content.
_TOKEN_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]{24,}$")

# A long digit run is treated as a card/account number, not a label.
_LONG_DIGITS = re.compile(r"^(?:\d[\s\-]?){12,}$")

_ALLOWED_FIELD_CATEGORIES = frozenset(
    {
        "text",
        "textarea",
        "search",
        "email",
        "tel",
        "url",
        "number",
        "date",
        "checkbox",
        "radio",
        "select",
        "other",
    }
)

# Categories whose values are never retained even though the interaction is.
_VALUE_FREE_CATEGORIES = frozenset(
    {"text", "textarea", "search", "email", "tel", "url", "number", "date", "other"}
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


class EventSanitizer:
    """Turns raw browser data into retainable evidence."""

    def sanitize_url(self, raw: object) -> tuple[str, bool]:
        """Return ``(sanitized_url, redaction_applied)``.

        Never raises: malformed input yields an empty string and a redaction
        flag so the event is still recorded with a missing-state indicator.
        """
        if raw is None:
            return "", False
        text = str(raw).strip()
        if not text:
            return "", False
        if text.startswith("about:"):
            return "about:blank", text != "about:blank"

        try:
            parts = urlsplit(text)
        except ValueError:
            return "", True

        scheme = parts.scheme.lower()
        if scheme not in {"http", "https", "file"}:
            # chrome://, data:, javascript: and friends are out of scope.
            return "", True

        redacted = bool(parts.query or parts.fragment or parts.username or parts.password)

        if scheme == "file":
            netloc = ""
            path, path_redacted = self._sanitize_path(parts.path)
            url = urlunsplit((scheme, netloc, path, "", ""))
        else:
            try:
                host = (parts.hostname or "").lower()
                port = parts.port
            except ValueError:
                return "", True
            if not host:
                return "", True
            default_port = (scheme == "http" and port == 80) or (
                scheme == "https" and port == 443
            )
            netloc = host if port is None or default_port else f"{host}:{port}"
            path, path_redacted = self._sanitize_path(parts.path)
            url = urlunsplit((scheme, netloc, path, "", ""))

        if path_redacted:
            redacted = True
        if len(url) > MAX_URL_LENGTH:
            url = url[:MAX_URL_LENGTH]
            redacted = True
        return url, redacted

    @staticmethod
    def _sanitize_path(path: str) -> tuple[str, bool]:
        if not path:
            return "/", False
        changed = False
        kept: list[str] = []
        for segment in path.split("/"):
            if _TOKEN_SEGMENT.match(segment) and not segment.isalpha():
                kept.append("{token}")
                changed = True
            else:
                kept.append(segment)
        return "/".join(kept), changed

    def safe_text(self, value: object, limit: int = MAX_TEXT_LENGTH) -> str:
        """Collapse, strip and clip a page-provided string."""
        if value is None:
            return ""
        text = _CONTROL_CHARS.sub("", str(value))
        text = _WHITESPACE.sub(" ", text).strip()
        if len(text) > limit:
            text = text[:limit].rstrip()
        return text

    def sanitize_title(self, value: object) -> str:
        return self.safe_text(value, MAX_TITLE_LENGTH)

    def sanitize_tag(self, value: object) -> str:
        tag = self.safe_text(value, MAX_TAG_LENGTH).lower()
        return tag if tag.isalnum() or tag.replace("-", "").isalnum() else ""

    def sanitize_test_id(self, value: object) -> str:
        return self.safe_text(value, MAX_TEST_ID_LENGTH)

    def sanitize_locator(self, value: object) -> str:
        return self.safe_text(value, MAX_LOCATOR_LENGTH)

    def normalize_field_category(self, value: object) -> str | None:
        category = self.safe_text(value, 32).lower()
        if category in _ALLOWED_FIELD_CATEGORIES:
            return category
        return None

    def is_sensitive_hint(self, *hints: object) -> bool:
        """Conservative check over attribute/label hints for sensitive fields."""
        joined = " ".join(str(hint) for hint in hints if hint)
        if not joined:
            return False
        return bool(_SENSITIVE_HINT.search(joined))

    def is_sensitive_field(
        self,
        *,
        input_type: object = None,
        autocomplete: object = None,
        hints: tuple[object, ...] = (),
    ) -> bool:
        field_type = self.safe_text(input_type, 32).lower()
        if field_type in SENSITIVE_INPUT_TYPES:
            return True
        auto = self.safe_text(autocomplete, 64).lower()
        if auto in SENSITIVE_AUTOCOMPLETE:
            return True
        return self.is_sensitive_hint(*hints)

    def safe_field_value(self, category: str | None, value: object) -> str | None:
        """Return a retainable value for a control, or ``None`` when unsafe.

        Text-like controls always return ``None``: the actual typed value is
        never retained (spec U1 Part 6.3/Part 10). Only select-style labels that
        are not sensitive survive.
        """
        if category in _VALUE_FREE_CATEGORIES or category is None:
            return None
        text = self.safe_text(value, MAX_TEXT_LENGTH)
        if not text or self.is_sensitive_hint(text) or _LONG_DIGITS.match(text):
            return None
        return text


__all__ = [
    "MAX_LOCATOR_LENGTH",
    "MAX_TEXT_LENGTH",
    "MAX_TITLE_LENGTH",
    "MAX_URL_LENGTH",
    "SENSITIVE_AUTOCOMPLETE",
    "SENSITIVE_INPUT_TYPES",
    "EventSanitizer",
]
