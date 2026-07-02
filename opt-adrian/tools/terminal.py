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


def _sim_nao(v) -> Optional[str]:
    return "sim" if v is True else ("não" if v is False else None)


_METODO_LABEL = {
    "financiamento": "Financiamento",
    "financiamento_100": "Financiamento 100%",
    "consorcio": "Consórcio",
    "avista": "À vista",
    "cartao": "Cartão",
    "troca": "Troca (integral)",
    "combinacao": "Combinação",
}

_DESFECHO_LABEL = {
    "qualificado_sem_agenda": "Qualificado — aguardando contato do vendedor",
    "qualificado_agendado": "Qualificado — visita agendada",
    "qualificado_fora_horario": "Qualificado — aguardando contato do vendedor",
    "handoff_solicitado": "Lead pediu atendimento humano",
    "handoff_erro": "Falha técnica — encaminhado ao vendedor",
}


def build_consolidated_note(
    state: SessionState, reason: str, motivo: Optional[str] = None
) -> str:
    """Resumo padronizado que o vendedor lê — só dados reais, sem invenção."""
    c = state.collected
    t = c.troca

    # Troca: sim + ficha / não / (não perguntado)
    if c.possui_troca is True:
        detalhe = " ".join(x for x in (t.modelo, t.ano) if x)
        extras = []
        if t.km:
            extras.append(f"{t.km} km")
        if t.quitado is not None:
            extras.append("quitado" if t.quitado else "com saldo")
        troca_txt = "sim" + (f" — {detalhe}" if detalhe else "")
        if extras:
            troca_txt += " (" + ", ".join(extras) + ")"
    else:
        troca_txt = _sim_nao(c.possui_troca)

    # Entrada: sim + valor / não
    if c.possui_entrada is True:
        entrada_txt = f"sim — {c.valor_entrada}" if c.valor_entrada else "sim (valor a confirmar)"
    else:
        entrada_txt = _sim_nao(c.possui_entrada)

    metodo = c.metodo_negociacao.value if c.metodo_negociacao else None
    metodo_txt = _METODO_LABEL.get(metodo, metodo)
    # rótulo da parcela depende do método (cartão = parcelamento)
    parcela_label = "Parcelamento" if metodo == "cartao" else "Faixa de parcela"

    blocks = [
        "📋 RESUMO ADRIAN — PRÉ-ATENDIMENTO",
        "\n[Identificação]",
        _line("Nome", c.nome),
        _line("Cidade", c.cidade),
        "\n[Interesse]",
        _line("Veículo de interesse", c.veiculo_interesse),
        _line("Confirmado", _sim_nao(c.veiculo_interesse_confirmado)),
        "\n[Negociação]",
        _line("Método", metodo_txt),
        _line("Troca", troca_txt),
        _line("Entrada", entrada_txt),
        _line(parcela_label, c.faixa_parcela),
        _line("Consórcio contemplado", _sim_nao(c.consorcio_contemplado)),
    ]
    # Situação só aparece se houver agendamento (booking lead-iniciado).
    if state.appointment.created and state.appointment.slot_iso:
        blocks += ["\n[Situação]", _line("Agendamento", state.appointment.slot_iso)]
    blocks += [
        "\n[Próximo passo]",
        _line("Desfecho", _DESFECHO_LABEL.get(reason, reason)),
        _line("Observação", motivo),
    ]
    return "\n".join(b for b in blocks if b)
