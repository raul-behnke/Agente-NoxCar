"""v2 — canonical event envelope + cost reconciliation + strict export auth.

Validates CONTRATO_EVENTOS_CANONICO alignment. Pure + DB, no LLM/network.
"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

os.environ["ADRIAN_DB_FILE"] = os.path.join(tempfile.gettempdir(), "adrian_v2_test.db")

import pytest  # noqa: E402

from db.engine import init_db  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    if os.path.exists(os.environ["ADRIAN_DB_FILE"]):
        os.remove(os.environ["ADRIAN_DB_FILE"])
    init_db()


def test_event_envelope_complete_and_unique():
    from db.events import events_since, record_event

    eid1 = record_event("HANDOFF_CREATED", "c1", "conv1", {"reason": "x"})
    eid2 = record_event("APPOINTMENT_CREATED", "c1", "conv1", {})
    rows = events_since(0)
    assert [r["event_id"] for r in rows] == [eid1, eid2]
    for r in rows:
        assert r["schema_version"] == 1
        assert r["client"] == "noxcar"
        assert r["agent"] == "adrian-noxcar"
        assert r["event_id"] and r["occurred_at"]
    # unique
    ids = [r["event_id"] for r in rows]
    assert len(ids) == len(set(ids))


def test_llm_call_cost_envelope_for_reconciliation():
    from db.usage import usage_since
    from usage import record_usage

    record_usage("voice", 2275, 730, model="gpt-5-mini",
                 contact_id="c1", conversation_id="conv1", reasoning_tokens=704)
    row = usage_since(0)[0]
    assert row["event_type"] == "LLM_CALL"
    assert row["event_id"] and row["client"] == "noxcar" and row["agent"] == "adrian-noxcar"
    p = row["payload"]
    for k in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens",
              "cost_brl", "cost_usd", "usd_brl_rate", "pricing_version"):
        assert k in p
    assert p["reasoning_tokens"] == 704
    assert p["usd_brl_rate"] == 5.40
    assert p["pricing_version"] == "placeholder-env"  # env fallback (no seed)


def test_whisper_event_ceils_minutes():
    from db.usage import usage_since
    from usage import record_audio_usage

    record_audio_usage("whisper", 90.0)  # 90s -> ceil 2 min
    row = [r for r in usage_since(0) if r["event_type"] == "WHISPER_TRANSCRIPTION"][0]
    assert row["payload"]["audio_seconds"] == 90
    assert round(row["payload"]["cost_brl"], 4) == round(2 * 0.006 * 5.40, 4)


def test_pricing_version_flows_after_seed():
    from db.usage import usage_since
    from scripts.seed_pricing import seed
    from usage import record_usage

    seed()
    record_usage("voice", 1_000_000, 0, model="gpt-5-mini")
    row = usage_since(0)[-1]
    assert row["payload"]["pricing_version"] == "2026-06-17"
    assert round(row["payload"]["cost_brl"], 4) == 1.35  # 0.25 * 5.40


def test_require_secret_strict_refuses_when_unset(monkeypatch):
    import security

    monkeypatch.setattr(security, "settings", SimpleNamespace(webhook_secret=""))
    assert security.require_secret_strict("anything") is False
    assert security.require_secret_strict(None) is False

    monkeypatch.setattr(security, "settings", SimpleNamespace(webhook_secret="s3cr3t"))
    assert security.require_secret_strict("s3cr3t") is True
    assert security.require_secret_strict("wrong") is False
    assert security.require_secret_strict(None) is False
