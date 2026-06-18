"""Mark abandoned conversations (Fase 2 / GAP-7).

`abandonado` existed in TerminalReason but was never set: there was no timeout
source. This sweep closes non-terminal sessions idle beyond abandon_idle_min and
emits CONVERSATION_ABANDONED so the Hub can measure drop-off.

Run from cron / systemd timer on the VPS, e.g. hourly:
    */60 * * * *  /opt/adrian/.venv/bin/python -m scripts.sweep_abandoned
"""
from __future__ import annotations

from config.settings import settings
from db.events import record_event
from db.sessions import init_db, load_or_new, save, stale_active_sessions
from obs import log
from tools.terminal import TerminalReason


def sweep(idle_minutes: int | None = None) -> int:
    init_db()
    idle = settings.abandon_idle_min if idle_minutes is None else idle_minutes
    contacts = stale_active_sessions(idle)
    n = 0
    for contact_id in contacts:
        state = load_or_new(contact_id)
        if state.terminal_reason:
            continue
        state.terminal_reason = TerminalReason.abandonado.value
        state.stage = "encerrado"
        save(state)
        record_event(
            "CONVERSATION_ABANDONED", contact_id, state.conversation_id,
            {"idle_minutes": idle, "followup_count": state.followup_count},
        )
        n += 1
    log.info("sweep_abandoned", marked=n, idle_minutes=idle)
    return n


if __name__ == "__main__":
    print(f"abandoned: {sweep()}")
