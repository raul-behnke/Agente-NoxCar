"""Updater (reference parity: agent/updater.py) — NON-Agno extraction stage.

Stage 1 of the per-turn pipeline. Reads the conversation and the current state,
returns a `StateUpdate` (structured). It does NOT write text to the lead. The
question to ask is decided later by the deterministic Question Planner (Sprint 3),
never here.

Model: gpt-5-mini, temperature 0.0 (Q10). All hard business rules stay in code;
this prompt only governs extraction quality.
"""
from __future__ import annotations

import json

from agent.schemas import Collected, SessionState, StateUpdate
from config.settings import settings
from llm import parse_structured

SYSTEM_PROMPT = """\
Você é o módulo de EXTRAÇÃO DE ESTADO de um atendimento comercial (revenda Nox Car).
Sua única tarefa é LER a conversa e devolver o estado estruturado. Você NUNCA escreve
mensagem para o lead.

Extraia, quando presentes de forma clara, os campos do funil:
- nome
- veiculo_interesse (modelo/carro citado)
- veiculo_interesse_confirmado (true só se o lead confirmou que é esse o veículo)
- metodo_negociacao: troca | financiamento | consorcio | avista | financiamento_100 | combinacao
- possui_troca (true/false conforme o lead disser; null se não disse)
- troca: modelo, ano, km, quitado (true/false), restante (valor aproximado)
- valor_entrada: valor que o lead dá de ENTRADA / sinal em dinheiro à vista.
  ATENÇÃO: um veículo na troca NÃO é valor_entrada. "financiar X" NÃO é entrada.
- valor_financiado: valor que o lead pretende FINANCIAR (ex.: "quero financiar 65 mil",
  "no máximo 65 mil financiado") -> valor_financiado = "65 mil" (NÃO valor_entrada).
- consorcio_contemplado (true/false se consórcio)
- interesse_agendamento (true se quer visitar/agendar; false se recusou; null se não tocou)

Sinais adicionais:
- pediu_humano: true se pediu explicitamente falar com pessoa/vendedor/consultor.
- conflito: true se mensagem agressiva, reclamação séria ou cenário emocional.
- quer_agendar: true se demonstrou intenção de marcar visita/horário.
- chosen_slot_iso: se o lead escolheu um horário específico, converta para ISO 8601
  COMPLETO com fuso -03:00, usando a DATA DE HOJE informada no contexto. Ex.: hoje é
  2026-06-15, lead diz "amanhã às 13h" -> "2026-06-16T13:00:00-03:00". Se o lead não
  deu horário concreto, deixe null (não invente).
- ai_identity_question: true se o lead perguntou se você é robô/IA/atendente automático.
- escalacao_pendente_motivo: se pediu ligação/simulação/negociação/avaliação fora do escopo.

Regras de extração:
- Amarre a interpretação à ÚLTIMA pergunta feita ao lead (use o estado atual).
- Aceite respostas não estruturadas e extraia o máximo (ex.: "Corolla 2017 quitado, dou mais 20 mil"
  -> troca: modelo=Corolla, ano=2017, quitado=true, restante=20 mil; metodo inclui troca).
- "financiamento 100%" / "sem entrada" -> metodo_negociacao = financiamento_100.
- NÃO invente. Se um dado não foi dito, deixe null. Não preencha por suposição.
- Só preencha campos com o que está EXPLÍCITO ou claramente inferível da conversa.
- Devolva apenas os campos que mudaram ou foram ditos; o merge cuida do resto.
"""


def _serialize_history(history: list[dict], limit: int = 30) -> str:
    """Compact last `limit` turns into a readable transcript for the model."""
    recent = history[-limit:]
    lines = []
    for m in recent:
        who = "LEAD" if m.get("direction") == "inbound" else "LOJA"
        body = (m.get("body") or m.get("message") or "").strip()
        if body:
            lines.append(f"{who}: {body}")
    return "\n".join(lines)


def validate_update(update: StateUpdate) -> StateUpdate:
    """Light anti-hallucination normalization (PRD §4.10).

    The Pydantic schema already constrains enums/types. Here we trim obviously
    empty strings to None so the merge rules treat them as 'no info'.
    """
    c = update.collected
    for field in ("nome", "veiculo_interesse", "valor_entrada"):
        val = getattr(c, field)
        if isinstance(val, str) and not val.strip():
            setattr(c, field, None)
    return update


def run_updater(
    history: list[dict],
    state: SessionState,
    last_message: str,
    model: str | None = None,
    pending_field: str | None = None,
    pending_question: str | None = None,
) -> StateUpdate:
    """Extract a StateUpdate from the conversation + current state.

    `pending_field`/`pending_question` describe what was most likely asked last
    turn so a SHORT reply ('sim', '210 mil', 'amanhã 17h') binds to the right
    field instead of being dropped (fixes the re-ask loop)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hoje = datetime.now(ZoneInfo(settings.app_timezone)).strftime("%Y-%m-%d (%A)")
    pending_block = ""
    if pending_field or pending_question:
        pending_block = (
            "\n\nPERGUNTA PENDENTE (a última feita ao lead):\n"
            f"  texto: {pending_question or '(campo) ' + str(pending_field)}\n"
            f"  campo_alvo: {pending_field}\n"
            "Se a ÚLTIMA MENSAGEM DO LEAD responde essa pergunta — MESMO curta — "
            "preencha o campo_alvo com o valor correspondente. Afirmativa nua "
            "('sim','isso','já está','tem') => true; negativa nua ('não','negativo') "
            "=> false; número/horário => o valor. Não re-pergunte o que já foi dito."
        )
    user_content = (
        f"DATA DE HOJE: {hoje} (fuso America/Sao_Paulo, -03:00)\n\n"
        f"ESTADO ATUAL (JSON):\n{state.collected.model_dump_json()}\n\n"
        f"HISTÓRICO RECENTE:\n{_serialize_history(history)}\n\n"
        f"ÚLTIMA MENSAGEM DO LEAD (rajada agregada):\n{last_message}"
        f"{pending_block}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    update = parse_structured(
        messages, StateUpdate, model=model or settings.model_id,
        temperature=0.0, component="updater",
    )
    return validate_update(update)
