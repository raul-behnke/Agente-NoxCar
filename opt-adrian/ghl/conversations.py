"""Conversations API — contact-centric (matches the real GHL behaviour).

GHL outbound send takes a contactId and auto-creates/routes the conversation
(verified: POST /conversations/messages with contactId -> 201 + conversationId).
History needs a conversationId, resolved from the contact via /conversations/search.
All public methods take `contact_id` so callers never juggle conversation ids.
"""
from __future__ import annotations

from typing import Any

from config.settings import settings


class ConversationsMixin:
    def resolve_conversation_id(self, contact_id: str) -> str | None:
        r = self._http.get(
            "/conversations/search",
            params={"locationId": settings.crm_location_id, "contactId": contact_id},
        )
        r.raise_for_status()
        convs = r.json().get("conversations", [])
        return convs[0]["id"] if convs else None

    def get_history(self, contact_id: str) -> list[dict[str, Any]]:
        """Full conversation history for a contact. Returns [] when there is no
        conversation yet (new lead) OR the lookup errors — a missing history is
        NOT an integration failure and must not trigger escalation."""
        try:
            conv_id = self.resolve_conversation_id(contact_id)
            if not conv_id:
                return []
            r = self._http.get(f"/conversations/{conv_id}/messages")
            r.raise_for_status()
            data = r.json()
            msgs = data.get("messages")
            if isinstance(msgs, dict):  # GHL nests messages.messages
                msgs = msgs.get("messages", [])
            return msgs or []
        except Exception:  # noqa: BLE001 - treat any lookup failure as "no history"
            return []

    # GHL routes type "SMS" through the connected channel and delivers reliably;
    # type "WhatsApp" returned 201 but failed delivery (reference uses SMS too).
    def send_message(self, contact_id: str, text: str) -> None:
        r = self._http.post(
            "/conversations/messages",
            json={"type": "SMS", "contactId": contact_id, "message": text},
        )
        r.raise_for_status()

    def send_attachment(self, contact_id: str, url: str) -> None:
        r = self._http.post(
            "/conversations/messages",
            json={"type": "SMS", "contactId": contact_id, "attachments": [url]},
        )
        r.raise_for_status()
