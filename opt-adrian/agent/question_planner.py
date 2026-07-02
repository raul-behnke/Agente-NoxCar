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
    _METODO_INTEGRAL,
    MetodoNegociacao,
    SessionState,
    StateUpdate,
    _troca_relevant,
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
    "veiculo_interesse": "Me conta, qual veículo você está procurando?",
    "veiculo_interesse_confirmado": "É esse mesmo que te interessou?",
    "possui_troca": "Você possui algum veículo para dar na troca?",
    "possui_entrada": "Você pretende dar alguma entrada na negociação?",
    "metodo_negociacao": (
        "Como você pretende fazer a negociação? Financiamento, consórcio, à vista ou no cartão?"
    ),
    "faixa_parcela": "Pra eu já te direcionar certo, qual faixa de parcela cabe no seu mês?",
    "valor_entrada": "Qual valor aproximadamente?",
    "consorcio_contemplado": "Sua carta de consórcio já está contemplada?",
    "cidade": "E você é aqui da região ou vem de outra cidade pra visitar a loja?",
    "troca.modelo": "Qual é o modelo, ano e versão do veículo?",
    "troca.ano": "E de que ano é esse veículo?",
    "troca.km": "Qual a quilometragem aproximada?",
    "troca.quitado": "O veículo está quitado?",
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


def _canonical_for(state: SessionState, field: str) -> Optional[str]:
    """Texto canônico do campo, com variações CONTEXTUAIS quando fizer sentido."""
    # entrada: fraseado depende de o lead ter (ou não) veículo na troca.
    if field == "possui_entrada" and state.collected.possui_troca is True:
        return "Além do veículo na troca, você pretende dar alguma entrada na negociação?"
    # parcela: no cartão a pergunta é sobre parcelar o valor.
    if field == "faixa_parcela" and state.collected.metodo_negociacao == MetodoNegociacao.cartao:
        return "Você pretende parcelar todo o valor no cartão?"
    # método: se o lead já tem troca/entrada, o método é sobre COMPLEMENTAR a diferença.
    if field == "metodo_negociacao" and (
        state.collected.possui_troca is True or state.collected.possui_entrada is True
    ):
        return ("Além disso, você pretende complementar a diferença como? "
                "À vista, financiamento, consórcio ou cartão?")
    return CANONICAL_QUESTIONS.get(field)


def _make(state: SessionState, field: str, intent: QuestionIntent) -> NextQuestion:
    """Build the NextQuestion for `field`, flagging exhaustion at the 2-attempt limit."""
    attempts = state.insist_attempts.get(field, 0)
    return NextQuestion(
        intent=intent,
        field=field,
        canonical_text=_canonical_for(state, field),
        exhausted=attempts >= settings.max_insist_attempts,
    )


def plan_next_question(
    state: SessionState,
    update: Optional[StateUpdate] = None,
) -> NextQuestion:
    c = state.collected

    # 1. terminal -> nothing to ask
    if state.terminal_reason:
        return NextQuestion(intent=QuestionIntent.nenhum)

    # (agendamento removido — o agente não agenda; funil completo vai pro handoff.)

    # 3. lead asked a question -> answer from FAQ, MAS ainda avança o funil no
    #    mesmo turno: responde a dúvida (intent=duvida) E carrega a próxima
    #    pergunta do funil como pergunta_alvo. Um pré-atendente responde e segue.
    #    Detecta dúvida por intent OU por topics (multi-intenção, paridade AMC):
    #    assim uma pergunta embutida numa resposta de funil não é ignorada.
    base = _funnel_next(state, update)
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
    state: SessionState, update: Optional[StateUpdate]
) -> NextQuestion:
    """A próxima ação do FUNIL (sem tratar dúvida/agendamento-explícito)."""
    c = state.collected
    skipped = set(state.skipped_fields)

    # 4-opções. Apresentação de OUTROS modelos em andamento (paridade AMC): quando
    # o lead pediu alternativas/quer outro veículo, o foco do turno é a lista —
    # pergunte "algum desses chamou sua atenção?" (não "é esse mesmo?").
    if update is not None:
        topics = set(update.topics or [])
        if update.intent_secundario:
            topics.add(update.intent_secundario)
        if "ver_outros_carros" in topics or update.intent == "apresentar":
            return NextQuestion(
                intent=QuestionIntent.foco,
                field=None,
                canonical_text="Algum desses chamou sua atenção?",
            )

    # 4. vehicle identified but not confirmed -> present/confirm (foco), ONE-SHOT.
    #    Se o lead já ENGAJOU a negociação (troca/entrada/método/nome), o interesse
    #    está implícito. Pergunta a confirmação UMA vez: se o lead não confirmar,
    #    segue pro funil em vez de re-perguntar "é esse mesmo?" (flag = 1 tentativa).
    ja_engajou = (
        c.nome
        or c.possui_troca is not None
        or c.possui_entrada is not None
        or c.metodo_negociacao is not None
        or c.troca.modelo is not None
    )
    foco_ja_perguntado = state.insist_attempts.get("veiculo_interesse_confirmado", 0) >= 1
    if (
        c.veiculo_interesse
        and c.veiculo_interesse_confirmado is not True
        and "veiculo_interesse_confirmado" not in skipped
        and not ja_engajou
        and not foco_ja_perguntado
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
        # só segue o thread de troca quando o método a torna relevante (financia/
        # parcela). À vista/cartão não aprofunda troca.
        if troca_tocada and _troca_relevant(c) and not c.troca.is_complete():
            sub = _next_troca_subfield(c.troca, skipped)
            if sub is not None:
                return _make(state, sub, QuestionIntent.funil)
        entrada_tocada = uc.possui_entrada is True or bool(uc.valor_entrada)
        metodo_financia = (
            c.metodo_negociacao is not None and c.metodo_negociacao not in _METODO_INTEGRAL
            and c.metodo_negociacao != MetodoNegociacao.troca
        )
        if (
            entrada_tocada
            and metodo_financia
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

    # 6. funil completo -> NÃO oferece agendamento; o orchestrator encerra em
    #    handoff (o vendedor dá sequência). Nada a perguntar.
    return NextQuestion(intent=QuestionIntent.nenhum)
