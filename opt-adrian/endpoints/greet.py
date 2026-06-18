"""POST /sessions/{contact_id}/greet — proactive greeting (grill Q9).

Fired by a GHL workflow (e.g. MARKETPLACE) when a new marketplace lead arrives.
The payload is contact + custom fields (no conversationId / no chat message). We
seed the turn from "Veículo de Interesse" so Adrian opens about the right vehicle.
"""
from __future__ import annotations

from fastapi import APIRouter

from config.settings import settings
from endpoints.ingest import normalize_tags
from orchestrator import InboundEvent, process_turn, run_in_background

router = APIRouter()


@router.post("/sessions/{contact_id}/greet")
async def greet(contact_id: str, body: dict):
    tags = normalize_tags(body.get("tags"))
    veiculo = (body.get("Veículo de Interesse") or "").strip()
    msg = (body.get("Mensagem Completa") or "").strip()
    if not msg and veiculo:
        msg = f"Olá, tenho interesse no {veiculo}"

    ev = InboundEvent(
        message_id=body.get("messageId") or f"greet-{contact_id}-{body.get('date_created', '')}",
        contact_id=contact_id,
        conversation_id=contact_id,  # CRM ops are contact-centric
        message=msg,
        tags=tags,
        lead_name=body.get("full_name") or body.get("first_name"),
        lead_origin=body.get("contact_source"),
    )
    # return immediately; run the pipeline out of band (avoids GHL 60s timeout)
    run_in_background(process_turn(ev))
    return {"action": "accepted"}
