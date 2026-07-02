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


import re

# GHL anexa "Received on 📱[Canal]" / marcadores de tipo ao corpo — lixo que
# confunde a extração (e já disparou runaway de raciocínio no updater).
_JUNK_RE = re.compile(r"\s*Received on\s*[\U0001F300-\U0001FAFF]*\s*\[[^\]]*\].*$", re.DOTALL)
# marcadores de tipo do GHL: "> Voice Note <", "> Image <", "> Document <" etc.
_MARKER_RE = re.compile(r">\s*[^<>]{1,40}?\s*<")


def strip_received_on(text: str | None) -> str:
    t = _JUNK_RE.sub("", (text or ""))
    t = _MARKER_RE.sub(" ", t)
    return t.strip()


def _body(msg: dict[str, Any]) -> str:
    return strip_received_on(msg.get("body") or msg.get("message") or "")


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
    # tolerante: attachment pode ser string (URL) ou dict; áudio detectado por
    # type/mime "audio"/"voice" OU por extensão (.ogg/.oga/.opus/.mp3/.m4a/.wav).
    _AUDIO_EXT = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac", ".wav", ".amr")
    audio_urls = []
    for a in attachments:
        url = a if isinstance(a, str) else (a.get("url") if isinstance(a, dict) else None)
        if not url:
            continue
        tipo = str(a.get("type", "")).lower() if isinstance(a, dict) else ""
        u = url.lower().split("?")[0]
        if "audio" in tipo or "voice" in tipo or u.endswith(_AUDIO_EXT):
            audio_urls.append(url)
    contact_id = payload.get("contact_id") or payload.get("contactId")
    # GHL may send `message` as a string OR as an object {body, type, ...}.
    msg = payload.get("message")
    if isinstance(msg, dict):
        msg = msg.get("body") or msg.get("message") or ""
    text = strip_received_on(msg or payload.get("body") or payload.get("Mensagem Completa") or "")
    msg_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    message_id = (
        payload.get("message_id")
        or payload.get("messageId")
        or payload.get("id")
        or msg_obj.get("id")
        or msg_obj.get("messageId")
    )
    return {
        "message_id": message_id,
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


def is_superseded_by_outbound(messages: list[dict]) -> bool:
    """Dedup TEMPORAL (paridade AMC): True se a mensagem real mais recente da
    conversa é um OUTBOUND posterior ao último inbound — ou seja, o agente já
    respondeu depois da última fala do lead e este webhook é eco/retry antigo.

    Mais robusto que dedup por message_id (que o GHL manda None)."""
    real = [m for m in (messages or []) if not is_activity_event(m)]
    if not real:
        return False
    inbound = [m for m in real if m.get("direction") == "inbound"]
    if not inbound:
        return False
    latest_any = max(real, key=lambda m: m.get("dateAdded") or "")
    latest_in = max(inbound, key=lambda m: m.get("dateAdded") or "")
    return latest_any.get("direction") == "outbound" and (
        (latest_any.get("dateAdded") or "") > (latest_in.get("dateAdded") or "")
    )
