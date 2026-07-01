"""after-hours clock tests. Pure, deterministic — `now` injected."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agent.hours import is_after_hours

TZ = ZoneInfo("America/Sao_Paulo")


def _dt(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


# 2026-07-01 is a Wednesday. Anchor weekdays from there.
MON = (2026, 6, 29)
SAT = (2026, 7, 4)
SUN = (2026, 7, 5)


def test_monday_open_at_0800():
    assert is_after_hours(_dt(*MON, 8, 0)) is False


def test_monday_close_at_1830_is_after():
    # window is [08:00, 18:30) -> 18:30 itself is closed
    assert is_after_hours(_dt(*MON, 18, 30)) is True


def test_monday_1831_after():
    assert is_after_hours(_dt(*MON, 18, 31)) is True


def test_monday_border_0730_after():
    assert is_after_hours(_dt(*MON, 7, 30)) is True


def test_monday_midday_open():
    assert is_after_hours(_dt(*MON, 12, 0)) is False


def test_saturday_0900_open():
    assert is_after_hours(_dt(*SAT, 9, 0)) is False


def test_saturday_1300_after():
    assert is_after_hours(_dt(*SAT, 13, 0)) is True


def test_saturday_0859_after():
    assert is_after_hours(_dt(*SAT, 8, 59)) is True


def test_sunday_always_after():
    assert is_after_hours(_dt(*SUN, 11, 0)) is True


def test_naive_datetime_assumed_app_tz():
    # naive input should be treated as app timezone, not crash
    assert is_after_hours(datetime(2026, 7, 5, 11, 0)) is True
