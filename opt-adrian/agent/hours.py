"""Business-hours clock (PURE PYTHON, no I/O).

Single source of truth for "is the store open right now?". The agent switches to
after-hours mode (qualify only, never offer scheduling) whenever the local clock
falls outside every open window in `settings.business_hours`. Regra dura em
Python, não no prompt.

`now` is injectable so tests can pin the clock without monkeypatching time.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from config.settings import settings


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def is_after_hours(now: datetime | None = None) -> bool:
    """True when the store is closed at `now` (default = agora, app timezone).

    Consulta `settings.business_hours[weekday]`; None => fechado => True.
    Aberto no intervalo [abre, fecha); fora dele => True.
    """
    tz = ZoneInfo(settings.app_timezone)
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)

    window = settings.business_hours.get(now.weekday())
    if window is None:  # dia fechado
        return True

    abre, fecha = _parse_hhmm(window[0]), _parse_hhmm(window[1])
    return not (abre <= now.time() < fecha)
