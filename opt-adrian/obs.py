"""Structured JSON logging (structlog). Single entrypoint: get_logger().

NOTE: named `obs.py` (not `logging.py`) on purpose — a module named `logging`
shadows the stdlib module when the package dir is on sys.path and breaks any
code that does `import logging` (including venv). Keep this name.

Mirrors the reference's structlog setup so every pipeline step emits a parseable
JSON line. Falls back to the stdlib logger if structlog is not installed.
"""
from __future__ import annotations

try:
    import structlog

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        cache_logger_on_first_use=True,
    )

    def get_logger(name: str = "adrian"):
        return structlog.get_logger(name)

    def bind_ids(contact_id=None, conversation_id=None) -> None:
        """Bind identity to the logging context so cost rows + logs are
        attributable per conversation/lead (Fase 1 / GAP-1) without threading
        ids through every LLM seam."""
        structlog.contextvars.bind_contextvars(
            contact_id=contact_id, conversation_id=conversation_id
        )

    def clear_ids() -> None:
        structlog.contextvars.unbind_contextvars("contact_id", "conversation_id")

except ImportError:  # pragma: no cover - fallback path
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    def get_logger(name: str = "adrian"):
        return _logging.getLogger(name)

    def bind_ids(contact_id=None, conversation_id=None) -> None:
        pass

    def clear_ids() -> None:
        pass


log = get_logger()
