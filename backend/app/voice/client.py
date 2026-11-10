"""Small standard-library HTTP transport for the Gnani REST APIs. A3 owns it."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.voice.errors import VoiceAdapterError


def post_bytes(url: str, *, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    request = Request(url, data=body, headers=headers, method="POST")
    return _send(request, timeout)


def post_json(url: str, *, headers: dict[str, str], payload: dict[str, object], timeout: float) -> bytes:
    all_headers = {"Content-Type": "application/json", **headers}
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=all_headers,
        method="POST",
    )
    return _send(request, timeout)


def _send(request: Request, timeout: float) -> bytes:
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        status = exc.code
        if status in {401, 403}:
            code, message, retryable = "provider_auth_failed", "Gnani rejected the configured API key.", False
        elif status == 429:
            code, message, retryable = "provider_rate_limited", "Gnani is rate limiting requests; try again shortly.", True
        elif status in {400, 413, 415, 422}:
            code, message, retryable = "provider_invalid_request", "Gnani rejected the audio or speech request parameters.", False
        elif status >= 500:
            code, message, retryable = "provider_unavailable", "Gnani is temporarily unavailable; try again later.", True
        else:
            code, message, retryable = "provider_request_failed", f"Gnani request failed (HTTP {status}).", False
        raise VoiceAdapterError(code, message, retryable=retryable, status_code=status) from None
    except (TimeoutError, URLError, OSError) as exc:
        raise VoiceAdapterError(
            "provider_unreachable",
            "Could not reach Gnani; check connectivity and try again.",
            retryable=True,
        ) from None
