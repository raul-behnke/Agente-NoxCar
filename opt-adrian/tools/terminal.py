"""Terminal states + consolidated handoff note (reference parity: tools/terminal.py).

6 TERMINAL_REASONS (grill Q3). The note is the standardized §10 summary the human
consultant reads — built only from real state data (no invention, PRD §10.4).
Pure: no Agno/LLM.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from agent.schemas import SessionState


class TerminalReason(str, Enum):
    qualificado_agendado = "qualificado_agendado"
    qualificado_sem_agenda = "qualificado_sem_agenda"
    qualificado_fora_horario = "qualificado_fora_horario"
    handoff_solicitado = "handoff_solicitado"
    handoff_erro = "handoff_erro"
    inativo_sem_tag = "inativo_sem_tag"
    abandonado = "abandonado"


# reasons that require a note + workflow + tag removal (vs just stopping)
ACTIONABLE_TERMINALS = {
    TerminalReason.qualificado_agendado,
    TerminalReason.qualificado_sem_agenda,
    TerminalReason.qualificado_fora_horario,
    TerminalReason.handoff_solicitado,
    TerminalReason.handoff_erro,
}


def _line(label: str, val) -> str:
    return f"- {label}: {val}" if val not in (None, "") else ""


def build_consolidated_note(
    state: SessionState, reason: str, motivo: Optional[str] = None
) -> str:
    """Standardized fixed-block summary (PRD §10.3)."""
    c = state.collected
    t = c.troca

    # 3 eixos ortogonais: Troca / Entrada / Método (funding core).
    if c.possui_troca is True:
        troca_txt = (
            f"sim — {t.modelo or '?'} {t.ano or ''} km {t.km or '?'}"
            + (f" {'quitado' if t.quitado else 'com saldo'}" if t.quitado is not None else "")
        ).strip()
    elif c.possui_troca is False:
        troca_txt = "não"
    else:
        troca_txt = None  # não perguntado / parcial

    if c.possui_entrada is True:
        entrada_txt = f"sim — {c.valor_entrada}" if c.valor_entrada else "sim (valor não definido)"
    elif c.possui_entrada is False:
        entrada_txt = "não"
    else:
        entrada_txt = None

    blocks = [
        "📋 RESUMO ADRIAN — PRÉ-ATENDIMENTO",
        "\n[Identificação]",
        _line("Nome", c.nome),
        _line("Cidade", c.cidade),
        "\n[Interesse]",
        _line("Veículo de interesse", c.veiculo_interesse),
        _line("Confirmado", c.veiculo_interesse_confirmado),
        "\n[Negociação]",
        _line("Método", c.metodo_negociacao.value if c.metodo_negociacao else None),
        _line("Troca", troca_txt),
        _line("Entrada", entrada_txt),
        _line("Faixa de parcela", c.faixa_parcela),
        _line("Consórcio contemplado", c.consorcio_contemplado),
        "\n[Situação]",
        _line("Interesse em agendar", c.interesse_agendamento),
        _line("Agendamento", state.appointment.slot_iso if state.appointment.created else None),
        "\n[Próximo passo]",
        _line("Desfecho", reason),
        _line("Motivo", motivo),
    ]
    return "\n".join(b for b in blocks if b)
