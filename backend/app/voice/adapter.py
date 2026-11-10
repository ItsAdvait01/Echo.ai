"""Voice adapter starter. A3 owns provider integration and fallback behavior."""

from typing import Protocol


class VoiceAdapter(Protocol):
    def transcribe(self, audio: bytes) -> str: ...

    def speak(self, text: str) -> bytes: ...


class TextFallbackAdapter:
    """Explicit text-only fallback; not a real speech integration."""

    name = "text-fallback"

    def status(self) -> dict[str, str | bool]:
        return {"available": True, "mode": self.name}
