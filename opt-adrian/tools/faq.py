"""FAQ access (reference parity: tools/faq.py). NO RAG / NO vector store.

The FAQ is a YAML document stored in a GHL Custom Value (PRD §7.2). It is fetched
raw and injected into the voice agent's context; the agent answers strictly from
it (never from memory). A bundled local file is used as a dev fallback when the
CRM value is unavailable.
"""
from __future__ import annotations

from pathlib import Path

from ghl.client import crm
from obs import log

_LOCAL_FALLBACK = Path(__file__).resolve().parent.parent / "faq.yaml"


def get_faq_raw() -> str:
    """Return the raw FAQ YAML string (CRM Custom Value, cached 5min).

    Falls back to the bundled `faq.yaml` if the CRM read fails or is empty.
    """
    try:
        raw = crm.get_faq_yaml()
        if raw and raw.strip():
            return raw
    except Exception as exc:  # noqa: BLE001 - dev/offline fallback
        log.warning("faq_fetch_failed", error=str(exc))
    if _LOCAL_FALLBACK.exists():
        return _LOCAL_FALLBACK.read_text(encoding="utf-8")
    return ""
