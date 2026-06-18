"""Audio transcription (reference parity: audio/whisper.py). OpenAI whisper-1.

Async + kept OUT of the webhook accept path by callers (grill Q10): transcribe
before building the turn, concatenating multiple audios. Transcripts are ephemeral
(never persisted to state — PRD §9 / CLAUDE.md).
"""
from __future__ import annotations

import asyncio

from obs import log

_WHISPER_MODEL = "whisper-1"
_aclient = None


def _get_async_client():
    global _aclient
    if _aclient is None:
        from openai import AsyncOpenAI

        _aclient = AsyncOpenAI()
    return _aclient


async def transcribe_url(url: str) -> str:
    """Download an audio URL and transcribe it. Returns "" on failure (best-effort)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            audio_bytes = resp.content
        client = _get_async_client()
        result = await client.audio.transcriptions.create(
            model=_WHISPER_MODEL,
            file=("audio.ogg", audio_bytes),
            response_format="verbose_json",  # returns `duration` for cost accounting
        )
        # account Whisper cost (GAP-4): billed per audio minute, not tokens
        try:
            from db.events import record_event
            from usage import record_audio_usage

            duration = float(getattr(result, "duration", 0.0) or 0.0)
            record_audio_usage("whisper", duration)
            record_event("WHISPER_TRANSCRIPTION", payload={"seconds": round(duration, 2)})
        except Exception as acc_exc:  # noqa: BLE001 - accounting must not break transcription
            log.warning("whisper_usage_failed", error=str(acc_exc))
        return (result.text or "").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("whisper_failed", url=url, error=str(exc))
        return ""


async def transcribe_many(urls: list[str]) -> str:
    """Transcribe several audios concurrently and concatenate (chronological)."""
    if not urls:
        return ""
    texts = await asyncio.gather(*(transcribe_url(u) for u in urls))
    return "\n".join(t for t in texts if t)
