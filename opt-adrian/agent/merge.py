"""State merge rules (reference parity: merge_into_state).

Per-field policy from DECISOES_GRILL_ADRIAN.md + PRD §9.6/§12.7:
- fill-if-empty:  nome, troca subfields (str)
- override:       veiculo_interesse, metodo_negociacao, valor_entrada (latest context wins)
- sticky-True:    veiculo_interesse_confirmado (never regress to False/None)
- tri-state:      possui_troca, consorcio_contemplado, interesse_agendamento, troca.quitado
                  (None = "no info this turn" -> keep; False is a VALID value)
"""
from __future__ import annotations

from agent.schemas import Collected, SessionState, StateUpdate, TrocaInfo


def _fill_if_empty(current, incoming):
    return incoming if (current in (None, "") and incoming not in (None, "")) else current


def _override(current, incoming):
    return incoming if incoming not in (None, "") else current


def _sticky_true(current, incoming):
    return True if (current is True or incoming is True) else current


def _tri_state(current, incoming):
    return incoming if incoming is not None else current


def _merge_troca(current: TrocaInfo, inc: TrocaInfo) -> TrocaInfo:
    return TrocaInfo(
        modelo=_fill_if_empty(current.modelo, inc.modelo),
        ano=_fill_if_empty(current.ano, inc.ano),
        km=_fill_if_empty(current.km, inc.km),
        quitado=_tri_state(current.quitado, inc.quitado),
        restante=_fill_if_empty(current.restante, inc.restante),
    )


def _merge_collected(cur: Collected, inc: Collected) -> Collected:
    return Collected(
        nome=_fill_if_empty(cur.nome, inc.nome),
        veiculo_interesse=_override(cur.veiculo_interesse, inc.veiculo_interesse),
        veiculo_interesse_confirmado=_sticky_true(
            cur.veiculo_interesse_confirmado, inc.veiculo_interesse_confirmado
        ),
        metodo_negociacao=_override(cur.metodo_negociacao, inc.metodo_negociacao),
        possui_troca=_tri_state(cur.possui_troca, inc.possui_troca),
        troca=_merge_troca(cur.troca, inc.troca),
        valor_entrada=_override(cur.valor_entrada, inc.valor_entrada),
        valor_financiado=_override(cur.valor_financiado, inc.valor_financiado),
        consorcio_contemplado=_tri_state(cur.consorcio_contemplado, inc.consorcio_contemplado),
        interesse_agendamento=_tri_state(cur.interesse_agendamento, inc.interesse_agendamento),
    )


def merge_into_state(state: SessionState, update: StateUpdate) -> SessionState:
    """Return a new SessionState with the update merged in (does not mutate input)."""
    new = state.model_copy(deep=True)
    new.collected = _merge_collected(state.collected, update.collected)

    if update.ai_identity_question:
        new.ai_identity_asked_count = state.ai_identity_asked_count + 1
    if update.escalacao_pendente_motivo:
        new.escalacao_pendente_motivo = update.escalacao_pendente_motivo
    return new
