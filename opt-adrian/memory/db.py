"""DEPRECATED shim. Persistence moved to the `db/` package:
- db.sessions  -> SessionState + dedup/idempotency (Agno-free)
- db.agno_store -> Agno SqliteDb

Kept so existing imports keep working while callers migrate.
"""
from db.agno_store import agno_db  # noqa: F401
from db.sessions import (  # noqa: F401
    already_processed,
    bump_attempt,
    init_db,
    load_or_new,
    save,
    side_effect_done,
)

__all__ = [
    "agno_db", "init_db", "load_or_new", "save",
    "already_processed", "side_effect_done", "bump_attempt",
]
