"""Generic in-process TTL cache (reference parity: cache.py).

Used to absorb GHL Custom Value reads (inventory + FAQ) at a 5min TTL, keeping
them off the critical path on every turn. Not thread-safe by design — the app is
async single-process; callers await around it.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional


class TTLCache:
    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        hit = self._store.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic() + self.ttl, value)

    def get_or_set(self, key: str, producer: Callable[[], Any]) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = producer()
        self.set(key, value)
        return value

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)
