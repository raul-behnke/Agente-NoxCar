"""Sprint 8 — golden behavior tests over PRD scenarios.

Full orchestrator pipeline with mocked LLM seams (no quota needed). Asserts the
business outcomes the grill decisions require for representative conversations.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace

os.environ["ADRIAN_DB_FILE"] = os.path.join(tempfile.gettempdir(), "adrian_golden_test.db")

import pytest  # noqa: E402

import orchestrator as orch  # noqa: E402
from agent.schemas import Collected, MetodoNegociacao, StateUpdate, TrocaInfo  # noqa: E402
from db.sessions import init_db, load_or_new  # noqa: E402
from tools.terminal import TerminalReason  # noqa: E402


class FakeCrm:
    def __init__(self):
        self.sent = []
        self.workflow_adds = 0
        self.removed_tags = []
        self.notes = []
        self.appointments = []

    def get_history(self, conv):
        return []

    def send_message(self, conv, text):
        self.sent.append(text)

    def send_attachment(self, conv, url):
        self.sent.append(f"[PHOTO]{url}")

    def add_note(self, contact, body):
        self.notes.append(body)

    def add_to_workflow(self, contact, workflow_id=None):
        self.workflow_adds += 1

    def remove_tag(self, contact, tag):
        self.removed_tags.append(tag)

    def create_appointment(self, contact, slot, title):
        self.appointments.append(slot)


@pytest.fixture(autouse=True)
def _fresh_db():
    if os.path.exists(os.environ["ADRIAN_DB_FILE"]):
        os.remove(os.environ["ADRIAN_DB_FILE"])
    init_db()


def _harness(monkeypatch, update, turn=None):
    crm = FakeCrm()
    monkeypatch.setattr(orch, "crm", crm)
    monkeypatch.setattr(orch, "_extract", lambda h, s, m: update)

    async def fake_gen(state, nq, upd, msg, history=None):
        return turn or SimpleNamespace(bubbles=["resposta"], shown_external_ids=[], photos=[])

    monkeypatch.setattr(orch, "_generate", fake_gen)
    return crm


def _ev(mid="m1"):
    return orch.InboundEvent(
        message_id=mid, contact_id="c1", conversation_id="conv1",
        message="msg", tags=["agente-ia"],
    )


def _run(ev):
    return asyncio.run(orch.run_turn(ev))


# --- Cenário: troca + financiamento (combinação) completo -----------------

def test_combinacao_complete_offers_scheduling_not_escalate(monkeypatch):
    upd = StateUpdate(collected=Collected(
        nome="João", veiculo_interesse="Compass", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.combinacao, possui_troca=True,
        troca=TrocaInfo(modelo="HB20", ano="2018", km="50000", quitado=True, restante="15000"),
        valor_entrada="10000",
    ))
    _harness(monkeypatch, upd)
    r = _run(_ev())
    # funnel complete, scheduling not yet decided -> agent offers (reply), no escalation
    assert r.action == "replied"
    assert load_or_new("c1").terminal_reason is None


# --- Cenário: combinação incompleta (falta km da troca) -------------------

def test_combinacao_incomplete_continues_collecting(monkeypatch):
    upd = StateUpdate(collected=Collected(
        nome="João", veiculo_interesse="Compass", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.combinacao, possui_troca=True,
        troca=TrocaInfo(modelo="HB20", ano="2018", quitado=True, restante="15000"),
        valor_entrada="10000",
    ))
    _harness(monkeypatch, upd)
    r = _run(_ev())
    assert r.action == "replied"  # keeps asking (troca.km missing), no escalation


# --- Cenário: à vista qualificado oferece agendamento ---------------------

def test_avista_offers_scheduling(monkeypatch):
    upd = StateUpdate(collected=Collected(
        nome="Ana", veiculo_interesse="Onix", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.avista,
    ))
    _harness(monkeypatch, upd)
    assert _run(_ev()).action == "replied"


# --- Cenário: consórcio não contemplado completa funil --------------------

def test_consorcio_collects_contemplation(monkeypatch):
    upd = StateUpdate(collected=Collected(
        nome="Ana", veiculo_interesse="Onix", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.consorcio,
    ))
    _harness(monkeypatch, upd)
    # consorcio_contemplado still None -> funnel not complete -> keeps collecting
    assert _run(_ev()).action == "replied"


# --- Cenário: pedido humano no meio do funil escala imediato --------------

def test_human_request_midfunnel_escalates(monkeypatch):
    upd = StateUpdate(
        collected=Collected(nome="João"), pediu_humano=True,
    )
    crm = _harness(monkeypatch, upd)
    r = _run(_ev())
    assert r.action == "escalated"
    assert crm.workflow_adds == 1
    assert load_or_new("c1").terminal_reason == TerminalReason.handoff_solicitado.value


# --- Cenário: foto enviada antes das bolhas -------------------------------

def test_photos_sent_before_bubbles(monkeypatch):
    upd = StateUpdate(collected=Collected(veiculo_interesse="Compass"))
    turn = SimpleNamespace(
        bubbles=["Olha esse!"], shown_external_ids=["1"], photos=["http://x.jpg"]
    )
    crm = _harness(monkeypatch, upd, turn=turn)
    _run(_ev())
    assert crm.sent == ["[PHOTO]http://x.jpg", "Olha esse!"]
