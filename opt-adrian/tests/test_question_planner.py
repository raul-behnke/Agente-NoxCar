"""Sprint 3 — Question Planner tests. Pure deterministic, no LLM/network."""
from __future__ import annotations

from agent.schemas import (
    Collected,
    MetodoNegociacao,
    SessionState,
    StateUpdate,
    TrocaInfo,
)
from agent.question_planner import CANONICAL_QUESTIONS, QuestionIntent, plan_next_question


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
    assert q.canonical_text == CANONICAL_QUESTIONS["nome"]


def test_after_name_asks_vehicle():
    q = plan_next_question(_state(nome="João"))
    assert q.field == "veiculo_interesse"


def test_vehicle_set_unconfirmed_goes_foco():
    # veículo identificado e lead AINDA não engajou (sem nome/troca/etc.) -> confirma
    q = plan_next_question(_state(veiculo_interesse="Compass"))
    assert q.intent == QuestionIntent.foco
    assert q.field == "veiculo_interesse_confirmado"


def test_options_presentation_asks_algum_desses():
    # lead pediu outros modelos -> foco de lista, não "é esse mesmo?"
    s = _state(veiculo_interesse="Onix")
    q = plan_next_question(s, StateUpdate(topics=["ver_outros_carros"]))
    assert q.intent == QuestionIntent.foco
    assert q.canonical_text == "Algum desses chamou sua atenção?"


def test_foco_is_one_shot():
    # foco perguntado 1x -> se não confirmar, segue pro funil (não repete)
    s = _state(veiculo_interesse="Onix")
    assert plan_next_question(s).field == "veiculo_interesse_confirmado"  # 1ª vez
    s.insist_attempts["veiculo_interesse_confirmado"] = 1  # já perguntou
    q = plan_next_question(s)
    assert q.field != "veiculo_interesse_confirmado"  # não repete -> funil (nome)
    assert q.field == "nome"


def test_engaged_lead_skips_confirm():
    # lead já engajou negociação (deu nome) -> não re-pergunta "é esse mesmo?"
    q = plan_next_question(_state(nome="J", veiculo_interesse="Compass"))
    assert q.field != "veiculo_interesse_confirmado"


def test_confirmed_asks_metodo():
    # nova ordem: após confirmar veículo, o 1º eixo é o MÉTODO
    q = plan_next_question(
        _state(nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True)
    )
    assert q.field == "metodo_negociacao"


def _confirmed(**extra) -> SessionState:
    # confirmado + método financiamento (p/ troca/entrada serem relevantes)
    base = dict(
        nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.financiamento,
    )
    base.update(extra)
    return _state(**base)


def _complete(**extra) -> SessionState:
    # funil completo: à vista (paga integral) + cidade.
    base = dict(
        nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True,
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
    s = _confirmed(possui_troca=False, possui_entrada=False)
    assert plan_next_question(s).field == "faixa_parcela"


def test_avista_skips_to_cidade():
    # à vista -> pula troca/entrada -> cidade
    s = _confirmed(metodo_negociacao=MetodoNegociacao.avista)
    assert plan_next_question(s).field == "cidade"


# --- scheduling / desfecho ------------------------------------------------

def test_funnel_complete_no_scheduling_offer():
    # não oferece agendamento; orchestrator faz handoff direto -> nada a perguntar
    q = plan_next_question(_complete())
    assert q.intent == QuestionIntent.nenhum


def test_follow_troca_thread_over_nome():
    # lead ainda sem nome, mas ESTE turno revelou troca -> segue troca (modelo),
    # não volta pra pergunta do nome (método financia -> troca relevante)
    s = _state(veiculo_interesse="Onix", veiculo_interesse_confirmado=True,
               metodo_negociacao=MetodoNegociacao.financiamento, possui_troca=True)
    s.collected.troca.ano = "2001"
    upd = StateUpdate(collected=Collected(possui_troca=True, troca=TrocaInfo(ano="2001")))
    q = plan_next_question(s, upd)
    assert q.field == "troca.modelo"


def test_follow_entrada_thread():
    s = _state(veiculo_interesse="Onix", veiculo_interesse_confirmado=True,
               metodo_negociacao=MetodoNegociacao.financiamento, possui_entrada=True)
    upd = StateUpdate(collected=Collected(possui_entrada=True))
    q = plan_next_question(s, upd)
    assert q.field == "valor_entrada"


def test_no_thread_signal_keeps_normal_order():
    # sem sinal de troca no update, ordem normal (nome primeiro)
    s = _state(veiculo_interesse="Onix", veiculo_interesse_confirmado=True,
               metodo_negociacao=MetodoNegociacao.financiamento, possui_troca=True)
    q = plan_next_question(s, StateUpdate())
    assert q.field == "nome"


def test_scheduling_intent_ignored_no_agendamento():
    # agendamento removido: pedido de agendar não vira intent agendamento -> funil
    s = _state(nome="J")
    q = plan_next_question(s, StateUpdate(quer_agendar=True))
    assert q.intent != QuestionIntent.agendamento


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


def test_duvida_via_topics_carries_funnel_question():
    # pergunta embutida (topics=duvida_operacional) mesmo com intent de funil
    # -> responde a dúvida E avança (não ignora a pergunta do lead)
    s = _state(veiculo_interesse="Onix", veiculo_interesse_confirmado=True)
    upd = StateUpdate(intent="qualificar", topics=["duvida_operacional"])
    q = plan_next_question(s, upd)
    assert q.intent == QuestionIntent.duvida
    assert q.field == "nome"


def test_duvida_still_carries_next_funnel_question():
    # lead faz pergunta E o funil pode avançar -> responde (duvida) + carrega a
    # próxima pergunta do funil no MESMO turno (pergunta_alvo preenchida)
    s = _state(veiculo_interesse="Onix", veiculo_interesse_confirmado=True)
    q = plan_next_question(s, StateUpdate(intent="duvida"))
    assert q.intent == QuestionIntent.duvida
    assert q.field == "nome"          # próxima pergunta do funil segue junto
    assert q.canonical_text is not None


def test_planner_skips_given_up_field():
    # name given up on -> planner moves to the next field instead of looping on it
    s = _state(veiculo_interesse="Onix", veiculo_interesse_confirmado=True)
    s.skipped_fields = ["nome"]
    q = plan_next_question(s)
    assert q.field == "metodo_negociacao"  # skipped nome, advanced to método (1º eixo)


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
