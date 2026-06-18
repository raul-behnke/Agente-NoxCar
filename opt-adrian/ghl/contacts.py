"""Contacts API: tags, notes (reference parity: ghl/contacts.py)."""
from __future__ import annotations


class ContactsMixin:
    def get_contact_tags(self, contact_id: str) -> list[str]:
        r = self._http.get(f"/contacts/{contact_id}")
        r.raise_for_status()
        return r.json().get("contact", {}).get("tags", [])

    def add_note(self, contact_id: str, body: str) -> None:
        r = self._http.post(f"/contacts/{contact_id}/notes", json={"body": body})
        r.raise_for_status()

    def remove_tag(self, contact_id: str, tag: str) -> None:
        """Used on terminal states to stop the agent acting (PRD §11.1)."""
        r = self._http.request(
            "DELETE", f"/contacts/{contact_id}/tags", json={"tags": [tag]}
        )
        r.raise_for_status()
