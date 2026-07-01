"""Question Planner (reference parity: agent/question_planner.py) — PURE PYTHON.

Stage 2 of the per-turn pipeline. Decides the SINGLE next question/intent
deterministically. The funnel question is ALWAYS decided here, never by the LLM
(grill Q-padrão). Resolves the three vices the reference calls out:
- repetida: rolling window `last_asked_fields` + 2-attempt limit (Q3/§4.8)
- ambígua: one field per turn, fixed phrasing in CANONICAL_QUESTIONS
- sem lógica: ordered by PRIORITY (compute_missing)

No LLM, no network — runs fully offline.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from agent.schemas import (
    MetodoNegociacao,
    SessionState,
    StateUpdate,
    compute_missing,
)
from config.settings import settings


class QuestionIntent(str, Enum):
    funil = "funil"            # next funnel field
    foco = "foco"              # present / confirm the vehicle
    agendamento = "agendamento"  # offer / confirm scheduling
    duvida = "duvida"          # lead asked something -> answer from FAQ
    nenhum = "nenhum"          # nothing to ask (terminal / awaiting outcome)


class NextQuestion(BaseModel):
    intent: QuestionIntent
    field: Optional[str] = None          # funnel field (or "troca.<sub>") targeted
    canonical_text: Optional[str] = None  # fixed phrasing; voice agent may soften it
    exhausted: bool = False              # 2 attempts already spent -> orchestrator escalates


CANONICAL_QUESTIONS: dict[str, str] = {
    "nome": "Como posso te chamar?",
    "veiculo_interesse": "Qual veículo você está procurando?",
    "veiculo_interesse_confirmado": "É esse veículo mesmo que você quer ver?",
    "possui_troca": "Você tem algum veículo para dar na troca?",
    "possui_entrada": "Você pretende dar algum valor de entrada?",
    "metodo_negociacao": (
        "Como você pretende fazer a negociação? Financiamento, à vista ou consórcio?"
    ),
    "faixa_parcela": "Qual faixa de parcela cabe no seu orçamento?",
    "valor_entrada": "Qual valor você tem de entrada?",
    "consorcio_contemplado": "Sua carta de consórcio já está contemplada?",
    "cidade": "De qual cidade você fala?",
    "troca.modelo": "Qual o modelo do veículo que você quer dar na troca?",
    "troca.ano": "Qual o ano desse veículo de troca?",
    "troca.km": "Quantos km ele tem, mais ou menos?",
    "troca.quitado": "Esse veículo de troca já está quitado?",
    "agendamento": "Quer agendar uma visita para ver o veículo de perto?",
}

_TROCA_SUBFIELDS = ("modelo", "ano", "km", "quitado")


def _next_troca_subfield(troca, skipped: set[str] | None = None) -> str | None:
    skipped = skipped or set()
    for sub in _TROCA_SUBFIELDS:
        key = f"troca.{sub}"
        if getattr(troca, sub) is None and key not in skipped:
            return key
    return None  # all set or remaining ones given up on


def _make(state: SessionState, field: str, intent: QuestionIntent) -> NextQuestion:
    """Build the NextQuestion for `field`, flagging exhaustion at the 2-attempt limit."""
    attempts = state.insist_attempts.get(field, 0)
    return NextQuestion(
        intent=intent,
        field=field,
        canonical_text=CANONICAL_QUESTIONS.get(field),
        exhausted=attempts >= settings.max_insist_attempts,
    )


def plan_next_question(
    state: SessionState,
    update: Optional[StateUpdate] = None,
    after_hours: bool = False,
) -> NextQuestion:
    c = state.collected

    # 1. terminal -> nothing to ask
    if state.terminal_reason:
        return NextQuestion(intent=QuestionIntent.nenhum)

    # 2. scheduling intent (premature allowed — grill Q4): explicit wish or chosen slot.
    #    Fora-do-horário: se o lead PEDIR agendar, ainda tratamos (booking segue ativo);
    #    o que o modo suprime é a OFERTA proativa (passo 6), não o pedido do lead.
    if update and (update.quer_agendar or update.chosen_slot_iso):
        return NextQuestion(
            intent=QuestionIntent.agendamento,
            canonical_text=CANONICAL_QUESTIONS["agendamento"],
        )

    # 3. lead asked a question -> answer from FAQ this turn
    if update and update.intent == "duvida":
        return NextQuestion(intent=QuestionIntent.duvida)

    skipped = set(state.skipped_fields)

    # 4. vehicle identified but not confirmed -> present/confirm (foco)
    if (
        c.veiculo_interesse
        and c.veiculo_interesse_confirmado is not True
        and "veiculo_interesse_confirmado" not in skipped
    ):
        return _make(state, "veiculo_interesse_confirmado", QuestionIntent.foco)

    # 5. funnel: first missing field not given up on, drilling into troca subfields
    for field in compute_missing(c):
        if field == "troca":
            sub = _next_troca_subfield(c.troca, skipped)
            if sub is None:
                continue
            return _make(state, sub, QuestionIntent.funil)
        if field in skipped:
            continue
        return _make(state, field, QuestionIntent.funil)

    # 6. funnel complete -> OFFER scheduling once (desfecho Q4).
    #    Fora-do-horário: suprime a oferta -> orchestrator encerra em
    #    qualificado_fora_horario (não há vendedor p/ dar sequência agora).
    if not after_hours and c.interesse_agendamento is None:
        return NextQuestion(
            intent=QuestionIntent.agendamento,
            canonical_text=CANONICAL_QUESTIONS["agendamento"],
        )

    # 7. complete + scheduling resolved (declined/booked) -> nothing to ask;
    #    orchestrator handles the outcome (book or escalate qualificado_sem_agenda)
    return NextQuestion(intent=QuestionIntent.nenhum)
