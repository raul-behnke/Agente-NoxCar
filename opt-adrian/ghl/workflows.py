"""Workflows API: add contact to the human-routing workflow on escalation
(reference parity: ghl/workflows.py; PRD §6.5)."""
from __future__ import annotations

from config.settings import settings


class WorkflowsMixin:
    def add_to_workflow(self, contact_id: str, workflow_id: str | None = None) -> None:
        wf = workflow_id or settings.escalation_workflow_id
        r = self._http.post(f"/contacts/{contact_id}/workflow/{wf}")
        r.raise_for_status()
