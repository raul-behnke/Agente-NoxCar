"""Composed GHL client (reference parity: ghl/client.py).

Single `crm` instance assembled from per-domain mixins. Network calls are
isolated here so agents/tools stay testable; all methods fail loud and the
orchestrator decides the fallback (usually: escalate to human — PRD §12.8).
"""
from __future__ import annotations

from ghl.base import GhlBase
from ghl.calendar import CalendarMixin
from ghl.contacts import ContactsMixin
from ghl.conversations import ConversationsMixin
from ghl.custom_values import CustomValuesMixin
from ghl.opportunities import OpportunitiesMixin
from ghl.workflows import WorkflowsMixin


class GhlClient(
    ContactsMixin,
    ConversationsMixin,
    CustomValuesMixin,
    WorkflowsMixin,
    CalendarMixin,
    OpportunitiesMixin,
    GhlBase,
):
    """Full GoHighLevel client used across the app."""


crm = GhlClient()
