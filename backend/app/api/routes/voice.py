"""
Voice / text command intake route.  Owned by A1.

Normalizes a transcript into a ``VoiceIntent`` and routes to the
appropriate backend action.  Actual STT/TTS is delegated to A3.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends

from app.core.dependencies import get_voice_adapter
from app.schemas.contracts import (
    VoiceCommandRequest,
    VoiceCommandResponse,
    VoiceIntent,
)
from app.voice.adapter import TextFallbackAdapter

router = APIRouter(prefix="/voice", tags=["voice"])

# ---------------------------------------------------------------------------
# Simple keyword-based intent classifier — sufficient for MVP text fallback.
# A3 may replace with an LLM-backed or Gnani-powered classifier later.
# ---------------------------------------------------------------------------
_INTENT_PATTERNS: list[tuple[re.Pattern[str], VoiceIntent]] = [
    # Teach start — must match before generic "continue/run" patterns
    (re.compile(r"\blearn\s+how\b", re.IGNORECASE), VoiceIntent.TEACH_START),
    (re.compile(r"\bstart\s+teach", re.IGNORECASE), VoiceIntent.TEACH_START),
    (
        re.compile(r"\b(teach|observe|watch|show)\b.*\b(start|begin|new)\b", re.IGNORECASE),
        VoiceIntent.TEACH_START,
    ),
    (
        re.compile(r"\b(start|begin)\b.*\b(teach|learn|observe|watch)\b", re.IGNORECASE),
        VoiceIntent.TEACH_START,
    ),
    # Teach stop — must match before bare "stop"
    (
        re.compile(r"\b(teach|learn|observe|watch)\b.*\b(stop|end|finish|done)\b", re.IGNORECASE),
        VoiceIntent.TEACH_STOP,
    ),
    (
        re.compile(r"\b(stop|end|finish)\b.*\b(teach|learn|observ)", re.IGNORECASE),
        VoiceIntent.TEACH_STOP,
    ),
    # Workflow execute
    (
        re.compile(
            r"\b(run|execute|do|continue|resume|start)\b.*\b(workflow|course|task|python)\b",
            re.IGNORECASE,
        ),
        VoiceIntent.WORKFLOW_EXECUTE,
    ),
    (re.compile(r"\bcontinue\b.*\b(my|the|python)\b", re.IGNORECASE), VoiceIntent.WORKFLOW_EXECUTE),
    # Execution control
    (re.compile(r"\bpause\b", re.IGNORECASE), VoiceIntent.EXECUTION_PAUSE),
    (
        re.compile(r"\bstop\b(?!.*\b(teach|learn|observ))", re.IGNORECASE),
        VoiceIntent.EXECUTION_STOP,
    ),
    # Status
    (
        re.compile(r"\b(status|progress|where am i|how.s it going)\b", re.IGNORECASE),
        VoiceIntent.STATUS_GET,
    ),
]


def _classify_intent(transcript: str) -> VoiceIntent | None:
    """Return the first matching intent, or None if unrecognised."""
    for pattern, intent in _INTENT_PATTERNS:
        if pattern.search(transcript):
            return intent
    return None


@router.post("/command", response_model=VoiceCommandResponse)
def voice_command(
    body: VoiceCommandRequest,
    adapter: TextFallbackAdapter = Depends(get_voice_adapter),
) -> VoiceCommandResponse:
    """Accept a text transcript (or voice-transcribed text) and normalise
    it into a ``VoiceIntent`` for the downstream action router.

    The route does **not** execute the action itself — the frontend or an
    orchestrator reads the intent and calls the appropriate API endpoint.
    """
    intent = _classify_intent(body.transcript)

    reply_text: str
    if intent is None:
        reply_text = (
            "I didn't understand that command. Try: "
            "'Start teaching', 'Continue my Python course', 'Pause', 'Stop', or 'Status'."
        )
    else:
        reply_text = _REPLY_TEMPLATES.get(intent, f"Understood: {intent.value}")

    return VoiceCommandResponse(
        intent=intent,
        transcript=body.transcript,
        action_result={"adapter": getattr(adapter, "name", "text-fallback")},
        reply_text=reply_text,
        source=body.source,
    )


_REPLY_TEMPLATES: dict[VoiceIntent, str] = {
    VoiceIntent.TEACH_START: "Starting teach mode. I'll observe your actions.",
    VoiceIntent.TEACH_STOP: "Stopping observation. Generating workflow draft.",
    VoiceIntent.WORKFLOW_EXECUTE: "Looking for a matching workflow to execute.",
    VoiceIntent.EXECUTION_PAUSE: "Pausing the current execution.",
    VoiceIntent.EXECUTION_STOP: "Stopping the current execution.",
    VoiceIntent.STATUS_GET: "Checking current status.",
}
