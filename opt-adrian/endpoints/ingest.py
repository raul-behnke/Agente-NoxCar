"""Inbound ingestion helpers (pure — no FastAPI/Agno/LLM, testable offline).

Implements the WhatsApp envelope logic (grill Q7): burst aggregation (GHL does
the debounce, so we aggregate inbound messages since the last real outbound),
activity-event filtering, and payload mapping. Audio transcription itself is in
audio/whisper.py (async/LLM); here we only collect the audio URLs.
"""
from __future__ import annotations

from typing import Any


def normalize_tags(tags: Any) -> list[str]:
    """GHL sends tags as a comma-separated string OR a list — normalize to list."""
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


def is_activity_event(msg: dict[str, Any]) -> bool:
    """GHL activity/system events (TYPE_ACTIVITY*) are not lead messages."""
    t = str(msg.get("type") or msg.get("messageType") or "")
    return t.upper().startswith("TYPE_ACTIVITY")


def _body(msg: dict[str, Any]) -> str:
    return (msg.get("body") or msg.get("message") or "").strip()


def last_inbound_burst(history: list[dict]) -> list[str]:
    """Inbound message bodies since the last real outbound (chronological)."""
    burst: list[str] = []
    for m in reversed(history):
        if is_activity_event(m):
            continue
        if m.get("direction") == "outbound":
            break
        body = _body(m)
        if body:
            burst.append(body)
    return list(reversed(burst))


def aggregate_burst(history: list[dict], incoming_text: str) -> str:
    """Merge the trailing inbound burst with the incoming message into one turn."""
    parts = last_inbound_burst(history)
    inc = (incoming_text or "").strip()
    if inc and (not parts or parts[-1] != inc):
        parts.append(inc)
    return "\n".join(parts) if parts else inc


def extract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a CRM webhook payload to the fields the orchestrator needs.

    Adjust the key names to your CRM's real schema (defaults target GHL).
    `audio_urls` collects voice attachments for transcription.
    """
    attachments = payload.get("attachments") or []
    audio_urls = [
        a.get("url")
        for a in attachments
        if isinstance(a, dict)
        and a.get("url")
        and str(a.get("type", "")).lower().startswith("audio")
    ]
    contact_id = payload.get("contact_id") or payload.get("contactId")
    # GHL may send `message` as a string OR as an object {body, type, ...}.
    msg = payload.get("message")
    if isinstance(msg, dict):
        msg = msg.get("body") or msg.get("message") or ""
    text = (msg or payload.get("body") or payload.get("Mensagem Completa") or "").strip()
    return {
        "message_id": payload.get("message_id") or payload.get("messageId"),
        "contact_id": contact_id,
        # CRM ops are contact-centric; conversation is resolved from the contact.
        "conversation_id": payload.get("conversation_id")
        or payload.get("conversationId")
        or contact_id,
        "tags": normalize_tags(payload.get("tags")),
        "text": text,
        "audio_urls": audio_urls,
        "lead_name": payload.get("contact_name")
        or payload.get("full_name")
        or payload.get("fullName"),
        "lead_origin": payload.get("source") or payload.get("contact_source"),
    }


def should_ignore(text: str, audio_urls: list[str]) -> bool:
    """Image/doc without text and no audio -> nothing to process (PRD §7 inbound)."""
    return not text and not audio_urls
