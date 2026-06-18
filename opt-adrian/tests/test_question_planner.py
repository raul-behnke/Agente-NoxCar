"""Sprint 3 — Question Planner tests. Pure deterministic, no LLM/network."""
from __future__ import annotations

from agent.schemas import (
    Collected,
    MetodoNegociacao,
    SessionState,
    StateUpdate,
    TrocaInfo,
)
from agent.question_planner import QuestionIntent, plan_next_question


def _state(**collected) -> SessionState:
    s = SessionState(contact_id="c1")
    for k, v in collected.items():
        setattr(s.collected, k, v)
    return s


# --- ordering / priority --------------------------------------------------

def test_empty_asks_name_first():
    q = plan_next_question(_state())
    assert q.intent == QuestionIntent.funil
    assert q.field == "nome"
    assert q.canonical_text == "Como posso te chamar?"


def test_after_name_asks_vehicle():
    q = plan_next_question(_state(nome="João"))
    assert q.field == "veiculo_interesse"


def test_vehicle_set_unconfirmed_goes_foco():
    q = plan_next_question(_state(nome="J", veiculo_interesse="Compass"))
    assert q.intent == QuestionIntent.foco
    assert q.field == "veiculo_interesse_confirmado"


def test_confirmed_asks_method():
    q = plan_next_question(
        _state(nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True)
    )
    assert q.field == "metodo_negociacao"


# --- troca drilldown ------------------------------------------------------

def test_troca_drills_into_subfields_in_order():
    s = _state(
        nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.troca,
    )
    assert plan_next_question(s).field == "troca.modelo"
    s.collected.troca.modelo = "HB20"
    assert plan_next_question(s).field == "troca.ano"
    s.collected.troca.ano = "2018"
    assert plan_next_question(s).field == "troca.km"
    s.collected.troca.km = "50000"
    assert plan_next_question(s).field == "troca.quitado"
    s.collected.troca.quitado = True
    assert plan_next_question(s).field == "troca.restante"


def test_financiamento_asks_entrada():
    s = _state(
        nome="J", veiculo_interesse="X", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.financiamento,
    )
    assert plan_next_question(s).field == "valor_entrada"


# --- scheduling / desfecho ------------------------------------------------

def test_funnel_complete_offers_scheduling():
    s = _state(
        nome="J", veiculo_interesse="X", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.avista,
    )
    q = plan_next_question(s)
    assert q.intent == QuestionIntent.agendamento


def test_premature_scheduling_intent_wins():
    # incomplete funnel but lead wants to schedule (grill Q4: allowed)
    s = _state(nome="J")
    q = plan_next_question(s, StateUpdate(quer_agendar=True))
    assert q.intent == QuestionIntent.agendamento


def test_scheduling_declined_yields_nenhum():
    s = _state(
        nome="J", veiculo_interesse="X", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.avista, interesse_agendamento=False,
    )
    assert plan_next_question(s).intent == QuestionIntent.nenhum


# --- guards ---------------------------------------------------------------

def test_terminal_yields_nenhum():
    s = _state()
    s.terminal_reason = "handoff_solicitado"
    assert plan_next_question(s).intent == QuestionIntent.nenhum


def test_duvida_intent_routes_to_faq():
    s = _state(nome="J")
    q = plan_next_question(s, StateUpdate(intent="duvida"))
    assert q.intent == QuestionIntent.duvida


def test_planner_skips_given_up_field():
    # name given up on -> planner moves to the next field instead of looping on it
    s = _state(veiculo_interesse="Onix", veiculo_interesse_confirmado=True)
    s.skipped_fields = ["nome"]
    q = plan_next_question(s)
    assert q.field == "metodo_negociacao"  # skipped nome, advanced


def test_planner_skips_exhausted_troca_subfield():
    s = _state(
        nome="J", veiculo_interesse="X", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.troca,
    )
    s.collected.troca.modelo = "HB20"
    s.skipped_fields = ["troca.ano"]
    q = plan_next_question(s)
    assert q.field == "troca.km"  # skipped ano, moved to km


def test_two_attempts_exhausts_field():
    s = _state()
    assert plan_next_question(s).exhausted is False
    s.insist_attempts["nome"] = 2
    q = plan_next_question(s)
    assert q.field == "nome"
    assert q.exhausted is True  # orchestrator escalates after this
