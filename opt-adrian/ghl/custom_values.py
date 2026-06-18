"""Custom Values API: stock JSON + FAQ YAML, both cached 5min
(reference parity: ghl/custom_values.py). The CRM is the source of truth; the
TTL cache keeps these reads off the per-turn critical path (PRD §7.1.1)."""
from __future__ import annotations

import json
from typing import Any

from cache import TTLCache
from config.settings import settings

_cache = TTLCache(ttl_seconds=300.0)


class CustomValuesMixin:
    def _get_custom_value_raw(self, custom_value_id: str) -> str:
        r = self._http.get(
            f"/locations/{settings.crm_location_id}/customValues/{custom_value_id}"
        )
        r.raise_for_status()
        return r.json().get("customValue", {}).get("value", "")

    def get_stock(self) -> list[dict[str, Any]]:
        """Live stock list (JSON in a Custom Value, synced every 3h — PRD §7.1.1).

        Accepts either a bare list or the wrapped shape {"vehicles": [...]}.
        """
        def _load() -> list[dict[str, Any]]:
            raw = self._get_custom_value_raw(settings.stock_custom_value_id) or "[]"
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict):
                return data.get("vehicles") or data.get("items") or []
            return data

        return _cache.get_or_set("stock", _load)

    def get_faq_yaml(self) -> str:
        """Raw FAQ YAML string from the CRM Custom Value (PRD §7.2)."""
        return _cache.get_or_set(
            "faq", lambda: self._get_custom_value_raw(settings.faq_custom_value_id)
        )
