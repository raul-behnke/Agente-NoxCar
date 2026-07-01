"""Core conversational state schemas (NÃO-Agno, determinístico).

Single source of truth for the qualification funnel. All decisions here come from
DECISOES_GRILL_ADRIAN.md (Q1–Q4):
- 9 funnel fields, order = PRD §5 commercial spine.
- conditional requiredness (troca=5 fields; valor_entrada if financing; etc.).
- avista / financiamento_100 carry no sub-fields.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MetodoNegociacao(str, Enum):
    troca = "troca"
    financiamento = "financiamento"
    consorcio = "consorcio"
    avista = "avista"
    financiamento_100 = "financiamento_100"
    combinacao = "combinacao"


# Conversational stages (PRD §9.5) — operational convenience, not ground truth.
STAGES = (
    "novo_contato", "aguardando_nome", "aguardando_veiculo_interesse",
    "veiculo_identificado", "aguardando_metodo_negociacao", "aguardando_dados_troca",
    "aguardando_valor_entrada", "aguardando_status_consorcio",
    "pronto_para_agendamento", "pronto_para_escalonamento", "agendamento_criado",
    "escalonado", "encerrado", "inativo_sem_tag",
)


class TrocaInfo(BaseModel):
    """Trade-in vehicle. Q2: the 5 fields are ALL required when trade is relevant."""
    modelo: Optional[str] = None
    ano: Optional[str] = None
    km: Optional[str] = None
    quitado: Optional[bool] = None
    restante: Optional[str] = None

    def is_complete(self) -> bool:
        # modelo/ano/km travam o funil; `quitado` é opcional e `restante` foi
        # removido do gate (mantido só como campo livre p/ retrocompat).
        return all(v is not None for v in (self.modelo, self.ano, self.km))


class Collected(BaseModel):
    """The 9 funnel fields (Q1), ordered per PRIORITY_FIELDS."""
    nome: Optional[str] = None
    veiculo_interesse: Optional[str] = None
    veiculo_interesse_confirmado: Optional[bool] = None
    metodo_negociacao: Optional[MetodoNegociacao] = None
    possui_troca: Optional[bool] = None
    troca: TrocaInfo = Field(default_factory=TrocaInfo)
    possui_entrada: Optional[bool] = None      # eixo entrada (gate p/ valor_entrada)
    valor_entrada: Optional[str] = None        # down payment / cash upfront (trade-in is NOT entrada)
    valor_financiado: Optional[str] = None     # amount the lead wants to FINANCE
    faixa_parcela: Optional[str] = None        # só se financiamento / financiamento_100
    consorcio_contemplado: Optional[bool] = None
    cidade: Optional[str] = None               # coletada SEMPRE (dentro e fora do horário)
    interesse_agendamento: Optional[bool] = None


PRIORITY_FIELDS = (
    "nome",
    "veiculo_interesse",
    "veiculo_interesse_confirmado",
    "possui_troca",
    "troca",
    "possui_entrada",
    "valor_entrada",
    "metodo_negociacao",
    "faixa_parcela",
    "consorcio_contemplado",
    "cidade",
    "interesse_agendamento",
)


def _troca_relevant(c: Collected) -> bool:
    # eixo troca é dirigido só pelo gate `possui_troca` (ortogonal ao método).
    return c.possui_troca is True


def compute_missing(c: Collected) -> list[str]:
    """Ordered list of fields still needed to COMPLETE the funnel (Q2).

    Recomputed live from `collected` (never trust an LLM-provided `missing`).
    `possui_troca` and `interesse_agendamento` are NOT completion gates.
    """
    missing: list[str] = []
    if not c.nome:
        missing.append("nome")
    if not c.veiculo_interesse:
        missing.append("veiculo_interesse")
    if c.veiculo_interesse_confirmado is not True:
        missing.append("veiculo_interesse_confirmado")

    # eixo troca (gate booleano -> subfields quando True)
    if c.possui_troca is None:
        missing.append("possui_troca")
    elif _troca_relevant(c) and not c.troca.is_complete():
        missing.append("troca")

    # eixo entrada (gate booleano -> valor quando True)
    if c.possui_entrada is None:
        missing.append("possui_entrada")
    elif c.possui_entrada is True and not (c.valor_entrada or c.valor_financiado):
        # either a down payment OR a financed amount satisfies the money question
        missing.append("valor_entrada")

    # método de negociação (funding core)
    m = c.metodo_negociacao
    if m is None:
        missing.append("metodo_negociacao")
    elif m in (MetodoNegociacao.financiamento, MetodoNegociacao.financiamento_100):
        if not c.faixa_parcela:
            missing.append("faixa_parcela")
    elif m == MetodoNegociacao.consorcio and c.consorcio_contemplado is None:
        missing.append("consorcio_contemplado")
    # avista / troca / combinacao -> sem sub-fields de método

    # cidade sempre exigida (dentro e fora do horário)
    if not c.cidade:
        missing.append("cidade")

    return missing


def funnel_complete(c: Collected) -> bool:
    return not compute_missing(c)


class Appointment(BaseModel):
    slot_iso: Optional[str] = None
    created: bool = False
    title: Optional[str] = None


class SessionState(BaseModel):
    """Persisted conversational state (db/sessions.py). CRM history stays the
    canonical record (PRD §9); this is operational support."""
    contact_id: str
    conversation_id: Optional[str] = None
    stage: str = "novo_contato"
    collected: Collected = Field(default_factory=Collected)
    # anti-repetition rolling window (Q2 / §4.6)
    last_asked_fields: list[str] = Field(default_factory=list)
    # per-field re-ask counter, limit 2 (Q3 / §4.8)
    insist_attempts: dict[str, int] = Field(default_factory=dict)
    # AI identity: evade 1st, admit 2nd (Q6)
    ai_identity_asked_count: int = 0
    # greeting done once -> don't re-greet on continuation turns
    saudacao_feita: bool = False
    # fields the lead won't provide after 2 tries -> stop asking, keep the conversation
    skipped_fields: list[str] = Field(default_factory=list)
    # out-of-scope escalation pending reason
    escalacao_pendente_motivo: Optional[str] = None
    # offer anti-repetition
    vehicles_shown: list[str] = Field(default_factory=list)
    last_card_external_id: Optional[str] = None
    appointment: Appointment = Field(default_factory=Appointment)
    terminal_reason: Optional[str] = None
    # commercial sync (Fase 3 / GAP-5): GHL opportunity id once created
    opportunity_id: Optional[str] = None
    # re-engagement (Fase 3 / GAP-7)
    followup_count: int = 0
    followup_pending: bool = False


class StateUpdate(BaseModel):
    """Output of the Updater (Sprint 2). Partial extraction merged into state.
    Defined here so schemas + merge live together."""
    collected: Collected = Field(default_factory=Collected)
    intent: Optional[str] = None
    # Multi-intenção por turno (paridade AMC): o lead pode tocar em vários
    # assuntos numa mensagem só ("É um Gol quitado. Atendem até que horas?").
    # topics é a fonte PRIMÁRIA; intent_secundario mantido p/ compat.
    # Valores canônicos: duvida_operacional | ver_outros_carros | pedido_foto | agendamento
    intent_secundario: Optional[str] = None
    topics: list[str] = Field(default_factory=list)
    pediu_humano: bool = False
    conflito: bool = False
    quer_agendar: bool = False
    chosen_slot_iso: Optional[str] = None
    ai_identity_question: bool = False
    escalacao_pendente_motivo: Optional[str] = None
