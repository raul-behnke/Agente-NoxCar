"""Re-engagement sweep (Fase 3 / GAP-7).

Sends a single follow-up nudge to non-terminal sessions idle beyond
followup_idle_min (but not yet abandoned), then marks followup_pending so the
next inbound turn emits FOLLOWUP_FINISHED (orchestrator.run_turn). Emits
FOLLOWUP_STARTED. Idle window between followup_idle_min and abandon_idle_min.

Run from cron / systemd timer, e.g. hourly:
    */60 * * * *  /opt/adrian/.venv/bin/python -m scripts.sweep_followups
"""
from __future__ import annotations

from config.settings import settings
from db.events import record_event
from db.sessions import init_db, load_or_new, save, stale_active_sessions
from ghl.client import crm
from obs import log

_NUDGE = "Oi! Ainda posso te ajudar a encontrar o carro certo? 🚗"


def sweep(idle_minutes: int | None = None) -> int:
    init_db()
    idle = settings.followup_idle_min if idle_minutes is None else idle_minutes
    # candidates idle past the followup window but not yet past abandonment
    candidates = set(stale_active_sessions(idle)) - set(
        stale_active_sessions(settings.abandon_idle_min)
    )
    n = 0
    for contact_id in candidates:
        state = load_or_new(contact_id)
        if state.terminal_reason or state.followup_pending:
            continue
        if state.followup_count >= settings.max_followups:
            continue
        try:
            crm.send_message(contact_id, _NUDGE)
        except Exception as exc:  # noqa: BLE001
            log.warning("followup_send_failed", contact_id=contact_id, error=str(exc))
            continue
        state.followup_count += 1
        state.followup_pending = True
        save(state)
        record_event("FOLLOWUP_STARTED", contact_id, state.conversation_id,
                     {"followup_count": state.followup_count})
        n += 1
    log.info("sweep_followups", sent=n, idle_minutes=idle)
    return n


if __name__ == "__main__":
    print(f"followups: {sweep()}")
