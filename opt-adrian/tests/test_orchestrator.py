"""Sprint 6 — orchestrator control-flow tests. Offline: LLM seams (_extract/
_generate) and the CRM client are monkeypatched. Verifies grill guardrails."""
from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace

os.environ["ADRIAN_DB_FILE"] = os.path.join(tempfile.gettempdir(), "adrian_orch_test.db")

import pytest  # noqa: E402

import orchestrator as orch  # noqa: E402
from agent.schemas import (  # noqa: E402
    Collected,
    MetodoNegociacao,
    SessionState,
    StateUpdate,
)
from db.sessions import init_db, load_or_new, save  # noqa: E402
from tools.terminal import TerminalReason  # noqa: E402


class FakeCrm:
    def __init__(self, history=None, fail_history=False):
        self._history = history or []
        self._fail_history = fail_history
        self.sent: list[str] = []
        self.notes: list[str] = []
        self.workflow_adds = 0
        self.removed_tags: list[str] = []
        self.appointments: list[str] = []

    def get_history(self, conv):
        if self._fail_history:
            raise RuntimeError("ghl down")
        return self._history

    def send_message(self, conv, text):
        self.sent.append(text)

    def add_note(self, contact, body):
        self.notes.append(body)

    def add_to_workflow(self, contact, workflow_id=None):
        self.workflow_adds += 1

    def remove_tag(self, contact, tag):
        self.removed_tags.append(tag)

    def create_appointment(self, contact, slot, title):
        self.appointments.append(slot)
        return {"id": "appt1"}

    def get_free_slots(self, days: int = 4):
        # default: the slot used by the booking test is available
        return getattr(self, "free_slots", ["2026-06-12T10:00"])


@pytest.fixture(autouse=True)
def _fresh_db():
    if os.path.exists(os.environ["ADRIAN_DB_FILE"]):
        os.remove(os.environ["ADRIAN_DB_FILE"])
    init_db()


def _ev(message_id="m1", tags=("agente-ia",), message="oi"):
    return orch.InboundEvent(
        message_id=message_id, contact_id="c1", conversation_id="conv1",
        message=message, tags=list(tags),
    )


def _patch(monkeypatch, crm, update=None, turn=None):
    monkeypatch.setattr(orch, "crm", crm)
    monkeypatch.setattr(orch, "_extract", lambda h, s, m: update or StateUpdate())

    async def fake_gen(state, nq, upd, msg, history=None):
        return turn or SimpleNamespace(bubbles=["Olá!"], shown_external_ids=[], photos=[])

    monkeypatch.setattr(orch, "_generate", fake_gen)


def _run(ev):
    return asyncio.run(orch.run_turn(ev))


# --- gates ----------------------------------------------------------------

def test_no_tag_ignored_and_marks_terminal(monkeypatch):
    crm = FakeCrm()
    _patch(monkeypatch, crm)
    r = _run(_ev(tags=()))
    assert r.action == "ignored"
    from db.sessions import load_or_new
    assert load_or_new("c1").terminal_reason == TerminalReason.inativo_sem_tag.value


def test_duplicate_message(monkeypatch):
    crm = FakeCrm()
    _patch(monkeypatch, crm)
    assert _run(_ev(message_id="dup")).action == "replied"
    assert _run(_ev(message_id="dup")).action == "duplicate"


def test_terminal_session_ignored(monkeypatch):
    s = SessionState(contact_id="c1")
    s.terminal_reason = TerminalReason.handoff_solicitado.value
    save(s)
    crm = FakeCrm()
    _patch(monkeypatch, crm)
    assert _run(_ev(message_id="x")).action == "ignored"


# --- immediate exceptions (Q4) -------------------------------------------

def test_explicit_human_escalates(monkeypatch):
    crm = FakeCrm()
    _patch(monkeypatch, crm, update=StateUpdate(pediu_humano=True))
    r = _run(_ev())
    assert r.action == "escalated"
    assert crm.workflow_adds == 1
    assert "agente-ia" in crm.removed_tags
    from db.sessions import load_or_new
    assert load_or_new("c1").terminal_reason == TerminalReason.handoff_solicitado.value


def test_conflict_escalates(monkeypatch):
    crm = FakeCrm()
    _patch(monkeypatch, crm, update=StateUpdate(conflito=True))
    assert _run(_ev()).action == "escalated"


def test_financiamento_100_is_NOT_immediate_escalation(monkeypatch):
    # grill Q4: financiamento_100 follows the normal flow (replies, not escalates)
    crm = FakeCrm()
    upd = StateUpdate(collected=Collected(metodo_negociacao=MetodoNegociacao.financiamento_100))
    _patch(monkeypatch, crm, update=upd)
    assert _run(_ev()).action == "replied"


# --- booking + desfecho ---------------------------------------------------

def test_chosen_slot_books(monkeypatch):
    crm = FakeCrm()
    _patch(monkeypatch, crm, update=StateUpdate(chosen_slot_iso="2026-06-12T10:00"))
    r = _run(_ev())
    assert r.action == "booked"
    assert crm.appointments == ["2026-06-12T10:00"]
    from db.sessions import load_or_new
    st = load_or_new("c1")
    assert st.appointment.created is True
    assert st.terminal_reason == TerminalReason.qualificado_agendado.value


def test_unavailable_slot_reoffers_not_books(monkeypatch):
    # lead picks a time the calendar doesn't offer -> re-offer, never blind-book
    crm = FakeCrm()
    crm.free_slots = ["2026-06-12T14:00", "2026-06-12T16:00"]
    _patch(monkeypatch, crm, update=StateUpdate(chosen_slot_iso="2026-06-12T10:00"))
    r = _run(_ev())
    assert r.action == "replied"
    assert crm.appointments == []  # nothing booked
    assert any("não está disponível" in b for b in crm.sent)
    from db.sessions import load_or_new
    assert load_or_new("c1").terminal_reason is None  # conversation stays open


def test_no_free_slots_escalates(monkeypatch):
    crm = FakeCrm()
    crm.free_slots = []
    _patch(monkeypatch, crm, update=StateUpdate(chosen_slot_iso="2026-06-12T10:00"))
    r = _run(_ev())
    assert r.action == "escalated"
    assert crm.appointments == []
    from db.sessions import load_or_new
    assert load_or_new("c1").terminal_reason == TerminalReason.qualificado_sem_agenda.value


def test_complete_funnel_declined_scheduling_escalates(monkeypatch):
    s = SessionState(contact_id="c1")
    s.collected = Collected(
        nome="J", veiculo_interesse="Compass", veiculo_interesse_confirmado=True,
        metodo_negociacao=MetodoNegociacao.avista, interesse_agendamento=False,
    )
    save(s)
    crm = FakeCrm()
    _patch(monkeypatch, crm)
    r = _run(_ev(message_id="z"))
    assert r.action == "escalated"
    from db.sessions import load_or_new
    assert load_or_new("c1").terminal_reason == TerminalReason.qualificado_sem_agenda.value


def test_two_attempts_exhaustion_skips_not_escalates(monkeypatch):
    # lead engaged but won't give the name after 2 tries -> skip it and keep
    # talking (NO premature escalation)
    s = SessionState(contact_id="c1")
    s.insist_attempts["nome"] = 2  # already asked twice
    save(s)
    crm = FakeCrm()
    _patch(monkeypatch, crm)
    r = _run(_ev(message_id="z"))
    assert r.action == "replied"
    assert "nome" in load_or_new("c1").skipped_fields


# --- normal turn ----------------------------------------------------------

def test_normal_reply_sends_bubbles(monkeypatch):
    crm = FakeCrm()
    turn = SimpleNamespace(bubbles=["Oi!", "Qual veículo?"], shown_external_ids=["1"], photos=[])
    _patch(monkeypatch, crm, turn=turn)
    r = _run(_ev())
    assert r.action == "replied"
    assert crm.sent == ["Oi!", "Qual veículo?"]
    from db.sessions import load_or_new
    assert load_or_new("c1").vehicles_shown == ["1"]


# --- integration failure --------------------------------------------------

def test_history_failure_safe_escalates(monkeypatch):
    crm = FakeCrm(fail_history=True)
    _patch(monkeypatch, crm)
    r = _run(_ev())
    assert r.action in ("escalated", "duplicate")
    from db.sessions import load_or_new
    assert load_or_new("c1").terminal_reason == TerminalReason.handoff_erro.value


# --- handoff idempotency (direct) ----------------------------------------

def test_handoff_idempotent_direct(monkeypatch):
    from tools.handoff import encaminhar_para_vendedor
    crm = FakeCrm()
    first = encaminhar_para_vendedor(crm, "c1", "conv1", "note", remove_tag_value="agente-ia")
    second = encaminhar_para_vendedor(crm, "c1", "conv1", "note", remove_tag_value="agente-ia")
    assert first["escalated"] is True
    assert second["duplicate"] is True
    assert crm.workflow_adds == 1  # not doubled
