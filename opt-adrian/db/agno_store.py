"""Agno session store. Isolated here because it imports Agno — keep persistence
(db/sessions.py) Agno-free so deterministic tests run without the LLM stack."""
from __future__ import annotations

from agno.db.sqlite import SqliteDb

from config.settings import settings

agno_db = SqliteDb(db_file=settings.db_file)
