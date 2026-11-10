"""Unit tests for URL/text/field sanitization. No browser needed."""

from __future__ import annotations

from app.browser.sanitizer import EventSanitizer

sanitizer = EventSanitizer()


def test_query_string_and_fragment_are_removed() -> None:
    url, redacted = sanitizer.sanitize_url(
        "https://Example.com/course/lesson-2?token=abc123&user=jane#section"
    )
    assert url == "https://example.com/course/lesson-2"
    assert redacted is True


def test_clean_url_is_unchanged_and_not_flagged() -> None:
    assert sanitizer.sanitize_url("https://example.com/course") == (
        "https://example.com/course",
        False,
    )


def test_explicit_port_is_kept_and_default_port_is_dropped() -> None:
    assert sanitizer.sanitize_url("http://127.0.0.1:8080/x")[0] == "http://127.0.0.1:8080/x"
    assert sanitizer.sanitize_url("https://example.com:443/x")[0] == "https://example.com/x"


def test_userinfo_is_removed() -> None:
    url, redacted = sanitizer.sanitize_url("https://user:pass@example.com/secret")
    assert url == "https://example.com/secret"
    assert "user" not in url
    assert "pass" not in url
    assert redacted is True


def test_token_shaped_path_segment_is_redacted() -> None:
    url, redacted = sanitizer.sanitize_url("https://example.com/reset/AbCdEf0123456789AbCdEf012345")
    assert url == "https://example.com/reset/{token}"
    assert redacted is True


def test_unsupported_schemes_are_dropped() -> None:
    assert sanitizer.sanitize_url("javascript:alert(1)") == ("", True)
    assert sanitizer.sanitize_url("data:text/html,<b>x</b>") == ("", True)


def test_about_blank_and_file_urls() -> None:
    assert sanitizer.sanitize_url("about:blank") == ("about:blank", False)
    url, redacted = sanitizer.sanitize_url("file:///C:/tmp/page.html?x=1")
    assert url == "file:///C:/tmp/page.html"
    assert redacted is True


def test_malformed_url_never_raises() -> None:
    assert sanitizer.sanitize_url("http://[::bad") == ("", True)
    assert sanitizer.sanitize_url(None) == ("", False)


def test_safe_text_collapses_and_clips() -> None:
    assert sanitizer.safe_text("  Next\n\n  lesson  ") == "Next lesson"
    assert len(sanitizer.safe_text("x" * 500)) == 80
    assert sanitizer.safe_text("bad\x00value\x07") == "badvalue"


def test_sensitive_hint_detection() -> None:
    for hint in (
        "user_password",
        "password",
        "cardNumber",
        "cvv",
        "api_key",
        "one-time-code",
        "ssn",
    ):
        assert sanitizer.is_sensitive_hint(hint), hint
    for hint in ("search", "author", "discard", "level", "next-lesson", "email"):
        assert not sanitizer.is_sensitive_hint(hint), hint


def test_sensitive_field_detection() -> None:
    assert sanitizer.is_sensitive_field(input_type="password")
    assert sanitizer.is_sensitive_field(autocomplete="cc-number")
    assert sanitizer.is_sensitive_field(hints=("user_password",))
    assert not sanitizer.is_sensitive_field(input_type="search", hints=("Search lessons",))


def test_values_are_only_retained_for_safe_non_text_controls() -> None:
    assert sanitizer.safe_field_value("text", "python loops") is None
    assert sanitizer.safe_field_value("search", "python loops") is None
    assert sanitizer.safe_field_value("textarea", "notes") is None
    assert sanitizer.safe_field_value(None, "anything") is None
    assert sanitizer.safe_field_value("select", "Intermediate") == "Intermediate"
    assert sanitizer.safe_field_value("select", "4111 1111 1111 1111") is None
    assert sanitizer.safe_field_value("select", "cvv") is None


def test_field_category_is_whitelisted() -> None:
    assert sanitizer.normalize_field_category("search") == "search"
    assert sanitizer.normalize_field_category("PASSWORD") is None
    assert sanitizer.normalize_field_category(None) is None
