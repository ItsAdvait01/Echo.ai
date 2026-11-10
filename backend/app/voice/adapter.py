"""Gnani Prisma/Timbre adapter and explicit text-only fallback. A3 owns this module."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
from pathlib import PurePath
from typing import Any, Protocol

from app.voice.client import post_bytes, post_json
from app.voice.errors import VoiceAdapterError, VoiceAdapterUnavailable

_STT_URL = "https://api.vachana.ai/stt/v3"
_TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
_SUPPORTED_AUDIO_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"}
_SUPPORTED_FORMATS = {"verbatim", "transcribe", "codemix", "transliterate"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


class VoiceAdapter(Protocol):
    """Provider-neutral surface for speech input and output."""

    @property
    def name(self) -> str: ...

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "command.wav",
        language_code: str | None = None,
        format: str | None = None,
        request_id: str | None = None,
    ) -> str: ...

    def speak(self, text: str) -> bytes: ...

    def status(self) -> dict[str, Any]: ...


class GnaniVoiceAdapter:
    """Synchronous REST adapter for short clips and spoken agent replies.

    Prisma REST accepts short uploaded clips (up to 30 seconds). For live
    streaming audio, use Gnani's streaming API in a separate adapter rather
    than buffering indefinitely through this interface.
    """

    name = "gnani"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        stt_url: str | None = None,
        tts_url: str | None = None,
        stt_model: str | None = None,
        stt_language: str | None = None,
        stt_format: str | None = None,
        tts_model: str | None = None,
        tts_voice: str | None = None,
        tts_language: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("GNANI_API_KEY", "")
        self.stt_url = stt_url or os.getenv("GNANI_STT_URL", _STT_URL)
        self.tts_url = tts_url or os.getenv("GNANI_TTS_URL", _TTS_URL)
        self.stt_model = stt_model or os.getenv("GNANI_STT_MODEL", "prisma-v3.0")
        self.stt_language = stt_language or os.getenv("GNANI_STT_LANGUAGE", "indic-auto")
        self.stt_format = stt_format or os.getenv("GNANI_STT_FORMAT", "transcribe")
        self.tts_model = tts_model or os.getenv("GNANI_TTS_MODEL", "timbre-v3.0")
        self.tts_voice = tts_voice or os.getenv("GNANI_TTS_VOICE", "Chetana")
        self.tts_language = tts_language or os.getenv("GNANI_TTS_LANGUAGE", "auto")
        self.timeout = timeout or float(os.getenv("GNANI_REQUEST_TIMEOUT_SECONDS", "30"))

    @classmethod
    def from_env(cls) -> GnaniVoiceAdapter:
        return cls()

    @property
    def configured(self) -> bool:
        return bool(self._api_key.strip())

    def status(self) -> dict[str, Any]:
        """Report local configuration only; this does not make a network call."""
        return {
            "provider": self.name,
            "available": self.configured,
            "mode": "gnani" if self.configured else "unconfigured",
            "stt_model": self.stt_model,
            "tts_model": self.tts_model,
            "message": "Gnani credentials configured." if self.configured else "GNANI_API_KEY is not set.",
        }

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "command.wav",
        language_code: str | None = None,
        format: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Transcribe a short audio clip with Prisma v3 REST."""
        self._require_key()
        if not audio:
            raise VoiceAdapterError("empty_audio", "The audio clip is empty.")
        if len(audio) > 16 * 1024 * 1024:
            raise VoiceAdapterError("audio_too_large", "Audio clips must be 16 MB or smaller.")

        safe_filename = PurePath(filename).name
        suffix = PurePath(safe_filename).suffix.lower()
        if suffix not in _SUPPORTED_AUDIO_SUFFIXES:
            allowed = ", ".join(sorted(_SUPPORTED_AUDIO_SUFFIXES))
            raise VoiceAdapterError("unsupported_audio_format", f"Use an audio file ending in {allowed}.")

        model = self.stt_model
        output_format = format or self.stt_format
        if model not in {"prisma-v2.5", "prisma-v3.0"}:
            raise VoiceAdapterError("invalid_stt_model", "Configured Prisma model is not supported.")
        if output_format not in _SUPPORTED_FORMATS:
            raise VoiceAdapterError("invalid_stt_format", "Configured transcript format is not supported.")
        if model == "prisma-v2.5" and output_format in {"codemix", "transliterate"}:
            raise VoiceAdapterError("invalid_stt_format", "This transcript format requires Prisma v3.0.")

        fields = {
            "model": model,
            "language_code": language_code or self.stt_language,
            "format": output_format,
        }
        boundary = "----EchoVoice" + secrets.token_hex(16)
        body = _multipart_body(fields, "audio_file", safe_filename, audio, boundary)
        headers = {
            "X-API-Key-ID": self._api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if request_id:
            if not _REQUEST_ID_RE.fullmatch(request_id):
                raise VoiceAdapterError("invalid_request_id", "Request ID contains unsupported characters.")
            headers["X-API-Request-ID"] = request_id

        raw = post_bytes(self.stt_url, headers=headers, body=body, timeout=self.timeout)
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VoiceAdapterError("invalid_provider_response", "Speech recognition returned an invalid response.") from exc
        if not isinstance(result, dict) or result.get("success") is False:
            raise VoiceAdapterError("transcription_failed", "Gnani could not transcribe this audio clip.")
        transcript = result.get("transcript")
        if not isinstance(transcript, str):
            raise VoiceAdapterError("invalid_provider_response", "Speech recognition response did not include a transcript.")
        return transcript

    def speak(self, text: str) -> bytes:
        """Synthesize a spoken reply with Timbre REST; returns the configured audio bytes."""
        self._require_key()
        if not text or not text.strip():
            raise VoiceAdapterError("empty_text", "Text to speak cannot be empty.")
        if len(text) > 5000:
            raise VoiceAdapterError("text_too_long", "Text to speak must be 5,000 characters or fewer.")
        if self.tts_model not in {"timbre-v2.5", "timbre-v3.0"}:
            raise VoiceAdapterError("invalid_tts_model", "Configured Timbre model is not supported.")
        if not self.tts_voice.strip():
            raise VoiceAdapterError("missing_tts_voice", "Configure a voice from the selected Timbre model.")

        payload = {
            "text": text,
            "voice": self.tts_voice,
            "model": self.tts_model,
            "language": self.tts_language,
            "audio_config": {
                "sample_rate": 24000,
                "num_channels": 1,
                "sample_width": 2,
                "encoding": "linear_pcm",
                "container": "wav",
            },
        }
        audio = post_json(
            self.tts_url,
            headers={"X-API-Key-ID": self._api_key},
            payload=payload,
            timeout=self.timeout,
        )
        if not audio:
            raise VoiceAdapterError("empty_audio_response", "Speech synthesis returned no audio.")
        return audio

    def _require_key(self) -> None:
        if not self.configured:
            raise VoiceAdapterUnavailable("GNANI_API_KEY is not configured; use the text fallback.")


class TextFallbackAdapter:
    """A1-compatible facade: uses Gnani when configured, otherwise text-only mode.

    Existing API wiring constructs this class. Keeping the class and its
    ``name`` attribute stable avoids requiring A1 to edit its dependency
    provider just to enable the optional provider adapter.
    """

    def __init__(self, provider: GnaniVoiceAdapter | None = None) -> None:
        candidate = provider or GnaniVoiceAdapter.from_env()
        self._provider = candidate if candidate.configured else None

    @property
    def name(self) -> str:
        return "gnani" if self._provider else "text-fallback"

    def transcribe(self, audio: bytes, **options: Any) -> str:
        if self._provider is None:
            raise VoiceAdapterUnavailable("Speech input is unavailable; enter the command as text.")
        return self._provider.transcribe(audio, **options)

    def speak(self, text: str) -> bytes:
        if self._provider is None:
            raise VoiceAdapterUnavailable("Speech output is unavailable; read the on-screen reply.")
        return self._provider.speak(text)

    def status(self) -> dict[str, Any]:
        if self._provider is None:
            return {
                "provider": "text-fallback",
                "available": True,
                "speech_input": False,
                "speech_output": False,
                "message": "Typed commands are available; Gnani credentials are not configured.",
            }
        return self._provider.status()


def _multipart_body(fields: dict[str, str], file_field: str, filename: str, payload: bytes, boundary: str) -> bytes:
    """Build the small multipart request using only the Python standard library."""
    safe_name = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    chunks: list[bytes] = []
    marker = boundary.encode("ascii")
    for name, value in fields.items():
        chunks.extend(
            [
                b"--" + marker + b"\r\n",
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    chunks.extend(
        [
            b"--" + marker + b"\r\n",
            f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_name}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
            payload,
            b"\r\n--" + marker + b"--\r\n",
        ]
    )
    return b"".join(chunks)
