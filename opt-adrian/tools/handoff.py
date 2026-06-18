"""Human handoff (reference parity: tools/handoff.py; PRD §6.5).

encaminhar_para_vendedor: note + add to human workflow + remove activation tag,
idempotent (PRD §12.3). Takes the CRM client as an argument so the orchestrator's
client is the single patch point in tests.
"""
from __future__ import annotations

from typing import Any, Optional

from db.sessions import side_effect_done


def encaminhar_para_vendedor(
    crm,
    contact_id: str,
    conversation_id: str,
    note_body: str,
    remove_tag_value: Optional[str] = None,
) -> dict[str, Any]:
    """1) consolidate note, 2) add to workflow, 3) remove tag. Idempotent."""
    if side_effect_done(conversation_id, "escalation"):
        return {"escalated": False, "duplicate": True}
    crm.add_note(contact_id, note_body)
    crm.add_to_workflow(contact_id)
    if remove_tag_value:
        try:
            crm.remove_tag(contact_id, remove_tag_value)
        except Exception:  # noqa: BLE001 - tag removal best-effort
            pass
    return {"escalated": True, "duplicate": False}
