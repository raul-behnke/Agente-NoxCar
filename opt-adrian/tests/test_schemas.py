"""Sprint 1 — deterministic tests for the state core (no LLM, no network).

Covers DECISOES_GRILL_ADRIAN.md Q1–Q4 + merge rules + persistence round-trip.
"""
from __future__ import annotations

import os
import tempfile

os.environ["ADRIAN_DB_FILE"] = os.path.join(tempfile.gettempdir(), "adrian_schemas_test.db")

import pytest  # noqa: E402

from agent.merge import merge_into_state  # noqa: E402
from agent.schemas import (  # noqa: E402
    Collected,
    MetodoNegociacao,
    SessionState,
    StateUpdate,
    TrocaInfo,
    compute_missing,
    funnel_complete,
)
from db.sessions import (  # noqa: E402
    already_processed,
    bump_attempt,
    init_db,
    load_or_new,
    save,
    side_effect_done,
)


@pytest.fixture(autouse=True)
def _db():
    # fresh DB file per run
    if os.path.exists(os.environ["ADRIAN_DB_FILE"]):
        os.remove(os.environ["ADRIAN_DB_FILE"])
    init_db()


def _base_complete() -> Collected:
    # modelo ortogonal: gates de troca/entrada resolvidos (False) + cidade + método.
    return Collected(
        nome="João",
        veiculo_interesse="Compass 2021",
        veiculo_interesse_confirmado=True,
        possui_troca=False,
        possui_entrada=False,
        metodo_negociacao=MetodoNegociacao.avista,
        cidade="Joinville",
    )


# --- compute_missing / funnel_complete (Q2) -------------------------------

def test_empty_collected_missing_base():
    # veiculo_interesse_confirmado NÃO é gate (confirmação = foco one-shot no planner)
    m = compute_missing(Collected())
    assert m == [
        "nome", "veiculo_interesse",
        "possui_troca", "possui_entrada", "metodo_negociacao", "cidade",
    ]


def test_gates_before_method_no_early_return():
    # sem early-return em metodo None: gates e cidade ainda aparecem
    c = Collected(nome="J", veiculo_interesse="X", veiculo_interesse_confirmado=True)
    assert compute_missing(c) == [
        "possui_troca", "possui_entrada", "metodo_negociacao", "cidade",
    ]


def test_avista_complete_with_base_only():
    assert funnel_complete(_base_complete()) is True


def test_cidade_always_required():
    c = _base_complete()
    c.cidade = None
    assert compute_missing(c) == ["cidade"]


def test_possui_entrada_true_requires_valor():
    c = _base_complete()
    c.possui_entrada = True
    assert compute_missing(c) == ["valor_entrada"]
    c.valor_entrada = "20000"
    assert funnel_complete(c) is True


def test_possui_entrada_false_skips_valor():
    c = _base_complete()
    c.possui_entrada = False
    assert "valor_entrada" not in compute_missing(c)


def test_financiamento_requires_faixa_parcela():
    c = _base_complete()
    c.metodo_negociacao = MetodoNegociacao.financiamento
    assert compute_missing(c) == ["faixa_parcela"]
    c.faixa_parcela = "até 1500"
    assert funnel_complete(c) is True


def test_financiamento_100_requires_faixa_parcela():
    c = _base_complete()
    c.metodo_negociacao = MetodoNegociacao.financiamento_100
    assert compute_missing(c) == ["faixa_parcela"]


def test_avista_no_faixa_parcela():
    c = _base_complete()
    c.metodo_negociacao = MetodoNegociacao.avista
    assert "faixa_parcela" not in compute_missing(c)


def test_troca_requires_modelo_ano_km_only():
    c = _base_complete()
    c.possui_troca = True
    assert compute_missing(c) == ["troca"]
    # quitado opcional, restante removido do gate
    c.troca = TrocaInfo(modelo="HB20", ano="2018", km="50000")
    assert funnel_complete(c) is True


def test_troca_gated_by_possui_troca_not_method():
    c = _base_complete()
    c.metodo_negociacao = MetodoNegociacao.financiamento
    c.faixa_parcela = "até 1500"
    c.possui_troca = True
    assert compute_missing(c) == ["troca"]


def test_consorcio_requires_contemplado():
    c = _base_complete()
    c.metodo_negociacao = MetodoNegociacao.consorcio
    assert compute_missing(c) == ["consorcio_contemplado"]
    c.consorcio_contemplado = False  # tri-state: False is valid
    assert funnel_complete(c) is True


def test_interesse_agendamento_not_a_gate():
    c = _base_complete()
    c.interesse_agendamento = None
    assert funnel_complete(c) is True  # None agendamento doesn't block completion


# --- merge_into_state -----------------------------------------------------

def test_merge_fill_if_empty_nome():
    s = SessionState(contact_id="c1")
    s2 = merge_into_state(s, StateUpdate(collected=Collected(nome="Ana")))
    assert s2.collected.nome == "Ana"
    # does not overwrite with empty
    s3 = merge_into_state(s2, StateUpdate(collected=Collected(nome=None)))
    assert s3.collected.nome == "Ana"


def test_merge_confirmado_sticky_true():
    s = SessionState(contact_id="c1")
    s.collected.veiculo_interesse_confirmado = True
    s2 = merge_into_state(s, StateUpdate(collected=Collected(veiculo_interesse_confirmado=False)))
    assert s2.collected.veiculo_interesse_confirmado is True  # never regresses


def test_merge_veiculo_override_latest_context():
    s = SessionState(contact_id="c1")
    s.collected.veiculo_interesse = "Onix"
    s2 = merge_into_state(s, StateUpdate(collected=Collected(veiculo_interesse="Compass")))
    assert s2.collected.veiculo_interesse == "Compass"


def test_merge_possui_troca_tristate_false_valid():
    s = SessionState(contact_id="c1")
    s2 = merge_into_state(s, StateUpdate(collected=Collected(possui_troca=False)))
    assert s2.collected.possui_troca is False
    # None keeps existing False
    s3 = merge_into_state(s2, StateUpdate(collected=Collected(possui_troca=None)))
    assert s3.collected.possui_troca is False


def test_merge_troca_deep_merge():
    s = SessionState(contact_id="c1")
    s = merge_into_state(s, StateUpdate(collected=Collected(troca=TrocaInfo(modelo="HB20"))))
    s = merge_into_state(s, StateUpdate(collected=Collected(troca=TrocaInfo(ano="2018"))))
    assert s.collected.troca.modelo == "HB20"
    assert s.collected.troca.ano == "2018"


def test_merge_ai_identity_counter_increments():
    s = SessionState(contact_id="c1")
    s = merge_into_state(s, StateUpdate(ai_identity_question=True))
    s = merge_into_state(s, StateUpdate(ai_identity_question=True))
    assert s.ai_identity_asked_count == 2


def test_merge_does_not_mutate_input():
    s = SessionState(contact_id="c1")
    merge_into_state(s, StateUpdate(collected=Collected(nome="X")))
    assert s.collected.nome is None  # original untouched


# --- persistence round-trip ----------------------------------------------

def test_session_roundtrip():
    s = SessionState(contact_id="c9", conversation_id="conv9")
    s.collected.nome = "Maria"
    s.collected.metodo_negociacao = MetodoNegociacao.troca
    s.insist_attempts["troca"] = 1
    s.terminal_reason = None
    save(s)
    loaded = load_or_new("c9")
    assert loaded.collected.nome == "Maria"
    assert loaded.collected.metodo_negociacao == MetodoNegociacao.troca
    assert loaded.insist_attempts["troca"] == 1


def test_load_or_new_creates_fresh():
    s = load_or_new("brand_new", "conv_x")
    assert s.contact_id == "brand_new"
    assert s.stage == "novo_contato"


# --- idempotency helpers --------------------------------------------------

def test_dedup_message():
    assert already_processed("m1") is False
    assert already_processed("m1") is True


def test_side_effect_idempotent():
    assert side_effect_done("conv1", "escalation") is False
    assert side_effect_done("conv1", "escalation") is True
    assert side_effect_done("conv1", "booking") is False  # different kind


def test_bump_attempt_limit():
    s = SessionState(contact_id="c1")
    assert bump_attempt(s, "troca") == 1
    assert bump_attempt(s, "troca") == 2
    assert s.insist_attempts["troca"] == 2
