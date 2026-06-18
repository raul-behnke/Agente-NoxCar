"""Calendar API: availability + appointment creation (GHL v2 correct format).

GHL free-slots expects startDate/endDate as EPOCH MILLIS (not date strings), and
appointment creation requires locationId + startTime/endTime (ISO) +
appointmentStatus. (Reference parity: zaf-amcveiculos calendar.py; PRD §8.)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from config.settings import settings


class CalendarMixin:
    def get_free_slots(self, days: int = 4) -> list[str]:
        tz = ZoneInfo(settings.app_timezone)
        now = datetime.now(tz)
        end = now + timedelta(days=days)
        r = self._http.get(
            f"/calendars/{settings.calendar_id}/free-slots",
            params={
                "startDate": int(now.timestamp() * 1000),
                "endDate": int(end.timestamp() * 1000),
            },
        )
        r.raise_for_status()
        data = r.json()
        # GHL nests slots per-day: {"2026-06-16": {"slots": [...]}, ...}
        slots: list[str] = []
        for v in data.values() if isinstance(data, dict) else []:
            if isinstance(v, dict):
                slots.extend(v.get("slots") or [])
        return slots

    def create_appointment(self, contact_id: str, slot_iso: str, title: str) -> dict[str, Any]:
        tz = ZoneInfo(settings.app_timezone)
        from dateutil import parser as dtparser  # lazy

        start = dtparser.isoparse(slot_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        end = start + timedelta(minutes=settings.appointment_duration_min)
        payload = {
            "calendarId": settings.calendar_id,
            "locationId": settings.crm_location_id,
            "contactId": contact_id,
            "startTime": start.isoformat(),
            "endTime": end.isoformat(),
            "title": title,
            "appointmentStatus": "confirmed",
        }
        r = self._http.post("/calendars/events/appointments", json=payload)
        r.raise_for_status()
        return r.json()
