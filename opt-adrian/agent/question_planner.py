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
    "nome": "Pra deixar seu atendimento certinho, como posso te chamar?",
    "veiculo_interesse": "Me conta, qual veículo você está procurando?",
    "veiculo_interesse_confirmado": "É esse mesmo que te interessou?",
    "possui_troca": "Você tem algum veículo hoje pra entrar como troca na negociação?",
    "possui_entrada": "E de entrada, você pensa em dar algum valor?",
    "metodo_negociacao": (
        "Como fica melhor pra você fechar: financiamento, à vista ou consórcio?"
    ),
    "faixa_parcela": "Pra eu já te direcionar certo, qual faixa de parcela cabe no seu mês?",
    "valor_entrada": "Quanto você pensa em dar de entrada?",
    "consorcio_contemplado": "Sua carta de consórcio já está contemplada?",
    "cidade": "E você é aqui da região ou vem de outra cidade pra visitar a loja?",
    "troca.modelo": "Qual o modelo do seu veículo atual?",
    "troca.ano": "E ele é de que ano?",
    "troca.km": "Tem ideia da quilometragem, mais ou menos?",
    "troca.quitado": "Ele já está quitado ou ainda tem parcela?",
    "agendamento": "Quer que eu já deixe uma visita reservada pra você ver de perto?",
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

    # 3. lead asked a question -> answer from FAQ, MAS ainda avança o funil no
    #    mesmo turno: responde a dúvida (intent=duvida) E carrega a próxima
    #    pergunta do funil como pergunta_alvo. Um pré-atendente responde e segue.
    #    Detecta dúvida por intent OU por topics (multi-intenção, paridade AMC):
    #    assim uma pergunta embutida numa resposta de funil não é ignorada.
    base = _funnel_next(state, update, after_hours)
    is_duvida = bool(update) and (
        update.intent == "duvida" or "duvida_operacional" in (update.topics or [])
    )
    if is_duvida:
        return NextQuestion(
            intent=QuestionIntent.duvida,
            field=base.field,
            canonical_text=base.canonical_text,
            exhausted=base.exhausted,
        )
    return base


def _funnel_next(
    state: SessionState, update: Optional[StateUpdate], after_hours: bool
) -> NextQuestion:
    """A próxima ação do FUNIL (sem tratar dúvida/agendamento-explícito)."""
    c = state.collected
    skipped = set(state.skipped_fields)

    # 4. vehicle identified but not confirmed -> present/confirm (foco).
    #    Se o lead já ENGAJOU a negociação (troca/entrada/método/nome), o interesse
    #    está implícito — não fique re-perguntando "é esse mesmo?".
    ja_engajou = (
        c.nome
        or c.possui_troca is not None
        or c.possui_entrada is not None
        or c.metodo_negociacao is not None
        or c.troca.modelo is not None
    )
    if (
        c.veiculo_interesse
        and c.veiculo_interesse_confirmado is not True
        and "veiculo_interesse_confirmado" not in skipped
        and not ja_engajou
    ):
        return _make(state, "veiculo_interesse_confirmado", QuestionIntent.foco)

    # 4b. FOLLOW THE LEAD'S THREAD: se a mensagem deste turno abriu um assunto do
    # funil (troca / entrada), continue ESSE fluxo em vez de voltar para campos
    # anteriores (ex.: nome). Um vendedor experiente segue o que o lead trouxe.
    if update is not None:
        uc = update.collected
        troca_tocada = uc.possui_troca is True or any(
            getattr(uc.troca, s) is not None for s in _TROCA_SUBFIELDS
        )
        if troca_tocada and c.possui_troca is True and not c.troca.is_complete():
            sub = _next_troca_subfield(c.troca, skipped)
            if sub is not None:
                return _make(state, sub, QuestionIntent.funil)
        entrada_tocada = uc.possui_entrada is True or bool(uc.valor_entrada)
        if (
            entrada_tocada
            and c.possui_entrada is True
            and not (c.valor_entrada or c.valor_financiado)
            and "valor_entrada" not in skipped
        ):
            return _make(state, "valor_entrada", QuestionIntent.funil)

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
    if not after_hours and c.interesse_agendamento is None:
        return NextQuestion(
            intent=QuestionIntent.agendamento,
            canonical_text=CANONICAL_QUESTIONS["agendamento"],
        )

    # 7. complete + scheduling resolved -> nothing to ask
    return NextQuestion(intent=QuestionIntent.nenhum)
