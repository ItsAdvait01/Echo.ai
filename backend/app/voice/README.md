# Gnani Voice Adapter (A3)

This package implements short-clip Prisma STT and synchronous Timbre TTS using Python's standard library. It uses Gnani's documented REST endpoints:

- STT: `POST https://api.vachana.ai/stt/v3` with multipart `audio_file`, `model`, `language_code`, and `format`.
- TTS: `POST https://api.vachana.ai/api/v1/tts/inference` with JSON; successful response is binary audio.

Official references: [Prisma REST transcription](https://docs.gnani.ai/api/STT/speech-to-text) and [Timbre REST synthesis](https://docs.gnani.ai/api/TTS/tts-inference).

## Configuration

Set secrets only in the local environment or an ignored `.env` loader. Do not commit API keys.

| Variable | Default | Purpose |
|---|---|---|
| `GNANI_API_KEY` | unset | Gnani API key sent as `X-API-Key-ID`. |
| `GNANI_STT_MODEL` | `prisma-v3.0` | Prisma model. |
| `GNANI_STT_LANGUAGE` | `indic-auto` | STT language hint for Prisma v3. |
| `GNANI_STT_FORMAT` | `transcribe` | Output format: verbatim, transcribe, codemix, or transliterate (last two require v3). |
| `GNANI_TTS_MODEL` | `timbre-v3.0` | Timbre model. |
| `GNANI_TTS_VOICE` | `Chetana` | Voice name belonging to the selected model. |
| `GNANI_TTS_LANGUAGE` | `auto` | Language of text sent to TTS. |
| `GNANI_REQUEST_TIMEOUT_SECONDS` | `30` | HTTP timeout. |
| `GNANI_STT_URL` / `GNANI_TTS_URL` | Official endpoints | Optional endpoint override for local/mock development. |

## Use

```python
from app.voice.adapter import GnaniVoiceAdapter, TextFallbackAdapter

adapter = GnaniVoiceAdapter.from_env()
status = adapter.status()  # local configuration only; does not ping Gnani
transcript = adapter.transcribe(audio_bytes, filename="command.wav")
audio_reply = adapter.speak("Teach mode is active.")

# Existing A1 dependency wiring constructs this facade. It selects Gnani only
# when GNANI_API_KEY is configured; otherwise speech methods fail clearly and
# text commands remain available.
voice = TextFallbackAdapter()
```

## Current scope and handoff

This adapter supports uploaded clips up to the provider's documented 30-second REST limit. It is not a live streaming microphone transport. The current A1 `/api/voice/command` route accepts an already-transcribed `transcript`; A1 must add an audio upload/stream route and call `transcribe()` for a browser microphone to use Prisma end to end. The current route already receives the facade, so its adapter status identifies Gnani vs text fallback when credentials are configured. TTS audio playback also needs a frontend/API response path owned by A1/A4.

No credentials were present during implementation, so no real-provider smoke request has been made. A 401/403, rate limit, invalid request, or network failure is returned as a typed `VoiceAdapterError` without exposing raw provider response bodies or credentials.
