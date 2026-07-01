"""SQLite engine + schema (operational support; NOT the canonical record — PRD §9).

Pure stdlib sqlite3 so the persistence layer imports with no Agno dependency
(keeps Sprint-1 deterministic tests runnable without the LLM stack). Postgres
JSONB migration is a later cycle.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

from config.settings import settings


def _db_path() -> str:
    """Resolve the DB path live (env wins) so tests can isolate per-file without
    fighting the frozen Settings import order."""
    return os.environ.get("ADRIAN_DB_FILE", settings.db_file)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    contact_id      TEXT PRIMARY KEY,
    state           TEXT NOT NULL,        -- SessionState JSON
    terminal_reason TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id   TEXT PRIMARY KEY,
    processed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS side_effects (
    conversation_id TEXT,
    kind            TEXT,   -- 'escalation' | 'booking'
    created_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (conversation_id, kind)
);
CREATE TABLE IF NOT EXISTS token_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    component         TEXT,
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost_brl          REAL,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS pricing (
    model          TEXT,
    kind           TEXT,    -- 'input' | 'output' | 'reasoning' | 'audio_minute'
    price_usd      REAL,    -- per 1M tokens (input/output/reasoning) or per minute (audio_minute)
    effective_from TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (model, kind, effective_from)
);
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    contact_id      TEXT,
    conversation_id TEXT,
    payload         TEXT,                 -- JSON, free-form per event_type
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_contact ON events(contact_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, created_at);
CREATE TABLE IF NOT EXISTS burst_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,  -- globally monotonic arrival token
    contact_id TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_burst_contact ON burst_tokens(contact_id, id);
"""

# Idempotent column additions. SQLite has no "ADD COLUMN IF NOT EXISTS", so we
# probe PRAGMA table_info and add only what's missing. Safe on every startup.
# token_usage: cost->conversation attribution (Fase 1) + canonical envelope for
# LLM_CALL/WHISPER reconciliation (v2 / CONTRATO_EVENTOS_CANONICO §3.1/§3.2).
_TOKEN_USAGE_COLUMNS = {
    "contact_id": "TEXT",
    "conversation_id": "TEXT",
    "total_tokens": "INTEGER",
    "reasoning_tokens": "INTEGER",
    "event_id": "TEXT",
    "cost_usd": "REAL",
    "usd_brl_rate": "REAL",
    "pricing_version": "TEXT",
}
# events: canonical envelope (v2 / CONTRATO §2).
_EVENTS_COLUMNS = {
    "event_id": "TEXT",
    "schema_version": "INTEGER DEFAULT 1",
    "client": "TEXT",
    "agent": "TEXT",
    "occurred_at": "TEXT",
}
# pricing: versioned exchange rate + version tag (CONTRATO §4).
_PRICING_COLUMNS = {
    "usd_brl_rate": "REAL",
    "pricing_version": "TEXT",
}


@contextmanager
def connect():
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _add_missing(con, table: str, columns: dict) -> None:
    existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
    for col, decl in columns.items():
        if col not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _migrate(con) -> None:
    _add_missing(con, "token_usage", _TOKEN_USAGE_COLUMNS)
    _add_missing(con, "events", _EVENTS_COLUMNS)
    _add_missing(con, "pricing", _PRICING_COLUMNS)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_token_usage_contact "
        "ON token_usage(contact_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_token_usage_event ON token_usage(event_id)"
    )
    # event_id is idempotency key (Hub dedups). UNIQUE index (not NOT NULL via
    # ALTER, since pre-v2 rows have NULL — SQLite allows multiple NULLs in UNIQUE).
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id)"
    )


def init_db() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        _migrate(con)
