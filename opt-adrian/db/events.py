"""Append-only business-event log (Fase 2 / GAP-10).

Durable record of state transitions the volatile Prometheus counters and the
overwritten `sessions` snapshot cannot give. The ZOI Performance Hub reads this
incrementally via /export/events?since=<id>.

v2 / CONTRATO_EVENTOS_CANONICO §2: every event carries the canonical envelope —
event_id (uuid4, idempotency key the Hub dedups on), schema_version, client,
agent, occurred_at (ISO8601 UTC). record_event fills them; events_since emits
them verbatim.

Event types (CONTRATO §3):
  CONVERSATION_STARTED, CONVERSATION_COMPLETED, CONVERSATION_ABANDONED,
  HANDOFF_CREATED, APPOINTMENT_CREATED, WHISPER_TRANSCRIPTION,
  OPPORTUNITY_CREATED, FOLLOWUP_STARTED, FOLLOWUP_FINISHED.
LLM_CALL is served by the token_usage table (canonical per-call cost feed).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from config.settings import settings
from db.engine import connect
from obs import log

SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_event(
    event_type: str,
    contact_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    payload: Optional[dict] = None,
    occurred_at: Optional[str] = None,
) -> str:
    """Persist one canonical event. Best-effort: never break a turn over telemetry.
    Returns the generated event_id (idempotency key)."""
    event_id = str(uuid.uuid4())
    try:
        with connect() as con:
            con.execute(
                "INSERT INTO events("
                "event_id, schema_version, event_type, client, agent, "
                "contact_id, conversation_id, occurred_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    SCHEMA_VERSION,
                    event_type,
                    settings.client_slug,
                    settings.agent_slug,
                    contact_id,
                    conversation_id,
                    occurred_at or _now_iso(),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("event_persist_failed", event_type=event_type, error=str(exc))
    return event_id


def events_since(since_id: int = 0, limit: int = 1000) -> list[dict]:
    """Incremental pull for the Hub collector — full canonical envelope (CONTRATO §2)."""
    with connect() as con:
        rows = con.execute(
            "SELECT id, event_id, schema_version, event_type, client, agent, "
            "contact_id, conversation_id, occurred_at, payload, created_at "
            "FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
            (int(since_id), int(limit)),
        ).fetchall()
    return [
        {
            "cursor_id": r["id"],
            "event_id": r["event_id"],
            "schema_version": r["schema_version"] or SCHEMA_VERSION,
            "event_type": r["event_type"],
            "client": r["client"] or settings.client_slug,
            "agent": r["agent"] or settings.agent_slug,
            "contact_id": r["contact_id"],
            "conversation_id": r["conversation_id"],
            "occurred_at": r["occurred_at"] or r["created_at"],
            "payload": json.loads(r["payload"] or "{}"),
        }
        for r in rows
    ]
