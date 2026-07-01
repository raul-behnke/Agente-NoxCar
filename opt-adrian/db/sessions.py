"""SessionState persistence + idempotency helpers (reference parity: db/sessions.py).

CRM history is canonical (PRD §9); this stores the inferred state plus dedup /
side-effect flags so webhook retries never double-act (PRD §12.3). Pure sqlite3.
"""
from __future__ import annotations

from agent.schemas import SessionState
from db.engine import connect, init_db

__all__ = [
    "init_db", "load_or_new", "save", "session_exists",
    "already_processed", "side_effect_done", "bump_attempt",
    "stale_active_sessions", "mark_inbound_arrival", "is_latest_inbound_arrival",
]


def mark_inbound_arrival(contact_id: str) -> int:
    """Register an inbound arrival; return a globally monotonic token.

    Shared across gunicorn workers (SQLite) so burst debounce coordinates even
    when the burst is load-balanced across worker processes."""
    with connect() as con:
        cur = con.execute(
            "INSERT INTO burst_tokens(contact_id) VALUES (?)", (contact_id,)
        )
        return int(cur.lastrowid)


def is_latest_inbound_arrival(contact_id: str, token: int) -> bool:
    """True if `token` is the newest arrival for the contact (no later message)."""
    with connect() as con:
        row = con.execute(
            "SELECT MAX(id) AS m FROM burst_tokens WHERE contact_id = ?", (contact_id,)
        ).fetchone()
    return bool(row) and row["m"] == token


def session_exists(contact_id: str) -> bool:
    with connect() as con:
        return con.execute(
            "SELECT 1 FROM sessions WHERE contact_id = ?", (contact_id,)
        ).fetchone() is not None


def stale_active_sessions(idle_minutes: int) -> list[str]:
    """contact_ids of non-terminal sessions idle longer than idle_minutes.

    Drives the abandonment sweep (Fase 2 / GAP-7): `abandonado` exists in the
    enum but was never set without a timeout source.
    """
    with connect() as con:
        rows = con.execute(
            "SELECT contact_id FROM sessions "
            "WHERE (terminal_reason IS NULL OR terminal_reason = '') "
            "AND updated_at <= datetime('now', ?)",
            (f"-{int(idle_minutes)} minutes",),
        ).fetchall()
    return [r["contact_id"] for r in rows]


def load_or_new(contact_id: str, conversation_id: str | None = None) -> SessionState:
    with connect() as con:
        row = con.execute(
            "SELECT state FROM sessions WHERE contact_id = ?", (contact_id,)
        ).fetchone()
    if row:
        return SessionState.model_validate_json(row["state"])
    return SessionState(contact_id=contact_id, conversation_id=conversation_id)


def save(state: SessionState) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO sessions(contact_id, state, terminal_reason) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(contact_id) DO UPDATE SET "
            "state = excluded.state, terminal_reason = excluded.terminal_reason, "
            "updated_at = datetime('now')",
            (state.contact_id, state.model_dump_json(), state.terminal_reason),
        )


def already_processed(message_id: str) -> bool:
    """Dedup inbound messages (kept alongside preemption — Q7)."""
    with connect() as con:
        if con.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone():
            return True
        con.execute("INSERT INTO processed_messages(message_id) VALUES (?)", (message_id,))
        return False


def side_effect_done(conversation_id: str, kind: str) -> bool:
    """Idempotency guard for escalation/booking (PRD §12.3)."""
    with connect() as con:
        if con.execute(
            "SELECT 1 FROM side_effects WHERE conversation_id = ? AND kind = ?",
            (conversation_id, kind),
        ).fetchone():
            return True
        con.execute(
            "INSERT INTO side_effects(conversation_id, kind) VALUES (?, ?)",
            (conversation_id, kind),
        )
        return False


def bump_attempt(state: SessionState, field: str) -> int:
    """Increment in-memory the re-ask counter for `field` (limit 2, Q3/§4.8).

    Operates on the SessionState (persisted via save()), not a side table, so the
    counter travels with the conversation snapshot.
    """
    state.insist_attempts[field] = state.insist_attempts.get(field, 0) + 1
    return state.insist_attempts[field]
