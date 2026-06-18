"""POST /webhook/inbound — the CRM calls this on each lead message.

Returns FAST (GHL times out at 60s); the 3-LLM pipeline runs in the background
and the reply reaches the lead via the CRM API. Flow: authenticate -> map ->
activity filter -> schedule background processing (audio->Whisper -> burst
aggregation -> orchestrator.process_turn with contactId preemption).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from audio.whisper import transcribe_many
from config.settings import settings
from endpoints.ingest import aggregate_burst, extract_payload, should_ignore
from ghl.client import crm
from obs import bind_ids, clear_ids, log
from orchestrator import InboundEvent, process_turn, run_in_background
from security import require_secret

router = APIRouter()


async def _process_inbound(data: dict) -> None:
    """Background: transcribe audio, aggregate the burst, run the turn."""
    # bind identity first so Whisper + the 3-LLM pipeline attribute cost per lead
    bind_ids(data["contact_id"], data["conversation_id"])
    try:
        await _process_inbound_inner(data)
    finally:
        clear_ids()


async def _process_inbound_inner(data: dict) -> None:
    audio_text = await transcribe_many(data["audio_urls"]) if data["audio_urls"] else ""
    text = "\n".join(t for t in (data["text"], audio_text) if t)
    if should_ignore(text, data["audio_urls"]):
        log.info("inbound_ignored_no_text", contact_id=data["contact_id"])
        return
    try:
        history = crm.get_history(data["contact_id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("history_fetch_failed", error=str(exc))
        history = []
    message = aggregate_burst(history, text)
    ev = InboundEvent(
        message_id=data["message_id"],
        contact_id=data["contact_id"],
        conversation_id=data["conversation_id"],
        message=message,
        tags=data["tags"],
        lead_name=data["lead_name"],
        lead_origin=data["lead_origin"],
    )
    await process_turn(ev)


@router.post("/webhook/inbound")
async def inbound(request: Request):
    payload = await request.json()
    if not require_secret(request.query_params.get("secret")):
        return {"action": "unauthorized"}

    data = extract_payload(payload)
    if settings.activation_tag not in data["tags"]:
        return {"action": "ignored", "detail": "sem tag"}

    # return immediately; process out of band (avoids GHL 60s timeout)
    run_in_background(_process_inbound(data))
    return {"action": "accepted"}
