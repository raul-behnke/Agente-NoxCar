"""Sprint 2 — Updater tests.

Two layers:
- unit (no network): serialization + anti-hallucination normalization.
- integration (skipped without OPENAI_API_KEY): real extraction over fixed
  transcripts, asserting the funnel fields/signals the grill decisions require.
"""
from __future__ import annotations

import os

import pytest

from agent.schemas import Collected, MetodoNegociacao, SessionState, StateUpdate
from agent.updater import _serialize_history, run_updater, validate_update

# Real-model evals are opt-in: they need a FUNDED key. Run with:
#   RUN_LLM_EVALS=1 OPENAI_API_KEY=sk-... pytest tests/test_updater.py
_RUN_EVALS = os.getenv("RUN_LLM_EVALS") == "1" and bool(os.getenv("OPENAI_API_KEY"))
_needs_key = pytest.mark.skipif(not _RUN_EVALS, reason="set RUN_LLM_EVALS=1 + funded key")


# --- unit ----------------------------------------------------------------

def test_serialize_history_formats_roles():
    hist = [
        {"direction": "inbound", "body": "oi"},
        {"direction": "outbound", "body": "Olá! Como posso ajudar?"},
        {"direction": "inbound", "body": ""},  # empty dropped
    ]
    out = _serialize_history(hist)
    assert "LEAD: oi" in out
    assert "LOJA: Olá! Como posso ajudar?" in out
    assert out.count("\n") == 1  # empty message skipped


def test_validate_update_trims_empty_strings():
    u = StateUpdate(collected=Collected(nome="   ", veiculo_interesse=""))
    v = validate_update(u)
    assert v.collected.nome is None
    assert v.collected.veiculo_interesse is None


def test_run_updater_plumbing_mocked(monkeypatch):
    """Verify run_updater builds system+user messages with state + last message,
    and applies validate_update — without calling the real API."""
    import agent.updater as upd

    captured = {}

    def fake_parse(messages, schema, model=None, temperature=None, component="llm"):
        captured["messages"] = messages
        captured["model"] = model
        captured["temperature"] = temperature
        captured["component"] = component
        # return an update with an empty-string name to prove validate runs
        return StateUpdate(collected=Collected(nome="  ", veiculo_interesse="Compass"))

    monkeypatch.setattr(upd, "parse_structured", fake_parse)

    state = SessionState(contact_id="t")
    state.collected.nome = None
    out = upd.run_updater(
        [{"direction": "inbound", "body": "quero um Compass"}],
        state,
        "última msg do lead",
    )

    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "última msg do lead" in msgs[1]["content"]
    assert "ESTADO ATUAL" in msgs[1]["content"]
    assert captured["temperature"] == 0.0          # Q10 determinism
    assert out.collected.nome is None              # validate_update trimmed "  "
    assert out.collected.veiculo_interesse == "Compass"


# --- integration (real model) -------------------------------------------

def _run(msg: str, state: SessionState | None = None) -> StateUpdate:
    return run_updater([], state or SessionState(contact_id="t"), msg)


@_needs_key
def test_extract_name_and_vehicle():
    u = _run("Oi, aqui é o João, queria ver um Compass")
    assert u.collected.nome and "joão" in u.collected.nome.lower()
    assert u.collected.veiculo_interesse and "compass" in u.collected.veiculo_interesse.lower()


@_needs_key
def test_financiamento_100_detected():
    u = _run("Quero financiar 100%, não tenho entrada")
    assert u.collected.metodo_negociacao == MetodoNegociacao.financiamento_100


@_needs_key
def test_trade_in_partial_inference():
    u = _run("Tenho um Corolla 2017 quitado e queria dar mais 20 mil")
    t = u.collected.troca
    assert t.modelo and "corolla" in t.modelo.lower()
    assert t.ano and "2017" in t.ano
    assert t.quitado is True
    assert u.collected.metodo_negociacao in (
        MetodoNegociacao.troca, MetodoNegociacao.combinacao,
    )


@_needs_key
def test_explicit_human_request():
    u = _run("Quero falar com um vendedor de verdade")
    assert u.pediu_humano is True


@_needs_key
def test_ai_identity_question():
    u = _run("vc é um robô?")
    assert u.ai_identity_question is True


@_needs_key
def test_no_invention_on_vague_message():
    u = _run("oi, tudo bem?")
    # nothing concrete -> no fabricated vehicle/price
    assert u.collected.veiculo_interesse is None
    assert u.collected.valor_entrada is None
