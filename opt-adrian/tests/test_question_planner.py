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


def test_confirmed_asks_possui_troca():
    # nova ordem: após confirmar veículo, primeiro gate é troca (não método)
    q = plan_next_question(
        _state(nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True)
    )
    assert q.field == "possui_troca"


def _confirmed(**extra) -> SessionState:
    base = dict(
        nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True,
    )
    base.update(extra)
    return _state(**base)


def _complete(**extra) -> SessionState:
    # funil completo (modelo ortogonal): gates resolvidos + método + cidade.
    base = dict(
        nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True,
        possui_troca=False, possui_entrada=False,
        metodo_negociacao=MetodoNegociacao.avista, cidade="Joinville",
    )
    base.update(extra)
    return _state(**base)


# --- troca drilldown ------------------------------------------------------

def test_troca_drills_into_subfields_in_order():
    s = _confirmed(possui_troca=True)
    assert plan_next_question(s).field == "troca.modelo"
    s.collected.troca.modelo = "HB20"
    assert plan_next_question(s).field == "troca.ano"
    s.collected.troca.ano = "2018"
    assert plan_next_question(s).field == "troca.km"
    s.collected.troca.km = "50000"
    # quitado é opcional -> assim que modelo/ano/km entram, troca deixa o funil
    assert plan_next_question(s).field != "troca.quitado"


def test_possui_entrada_true_asks_valor():
    s = _confirmed(possui_troca=False, possui_entrada=True)
    assert plan_next_question(s).field == "valor_entrada"


def test_financiamento_asks_faixa_parcela():
    s = _confirmed(
        possui_troca=False, possui_entrada=False,
        metodo_negociacao=MetodoNegociacao.financiamento,
    )
    assert plan_next_question(s).field == "faixa_parcela"


def test_cidade_asked_before_scheduling():
    s = _confirmed(
        possui_troca=False, possui_entrada=False,
        metodo_negociacao=MetodoNegociacao.avista,
    )
    assert plan_next_question(s).field == "cidade"


# --- scheduling / desfecho ------------------------------------------------

def test_funnel_complete_offers_scheduling():
    q = plan_next_question(_complete())
    assert q.intent == QuestionIntent.agendamento


def test_after_hours_suppresses_scheduling_offer():
    q = plan_next_question(_complete(), after_hours=True)
    assert q.intent == QuestionIntent.nenhum


def test_after_hours_still_qualifies_funnel():
    # fora-do-horário ainda pergunta os campos do funil normalmente
    q = plan_next_question(_confirmed(), after_hours=True)
    assert q.field == "possui_troca"


def test_after_hours_lead_request_still_schedules():
    # se o lead PEDIR agendar, atendemos mesmo fora-do-horário
    q = plan_next_question(_state(nome="J"), StateUpdate(quer_agendar=True), after_hours=True)
    assert q.intent == QuestionIntent.agendamento


def test_premature_scheduling_intent_wins():
    # incomplete funnel but lead wants to schedule (grill Q4: allowed)
    s = _state(nome="J")
    q = plan_next_question(s, StateUpdate(quer_agendar=True))
    assert q.intent == QuestionIntent.agendamento


def test_scheduling_declined_yields_nenhum():
    s = _complete(interesse_agendamento=False)
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
    assert q.field == "possui_troca"  # skipped nome, advanced to first gate


def test_planner_skips_exhausted_troca_subfield():
    s = _confirmed(possui_troca=True)
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
