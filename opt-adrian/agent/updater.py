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
- veiculo_interesse_confirmado: true SOMENTE quando o lead CONFIRMA o veículo
  APÓS a apresentação — "sim", "é esse", "isso", "gostei desse", "quero esse" —
  ou faz pergunta de NEGOCIAÇÃO sobre ele (troca, preço, financiamento, parcela).
  ATENÇÃO: a mensagem INICIAL de interesse ("tenho interesse no X", "quero ver o
  X", "vi o X") define veiculo_interesse mas NÃO confirma (deixe confirmado=null)
  — a confirmação é uma pergunta que o agente faz. Se o lead NEGA/quer outro
  ("não é esse", "queria um SUV", "tem outro?") => confirmado=false e topics
  inclui "ver_outros_carros".
- metodo_negociacao: troca | financiamento | consorcio | avista | cartao | financiamento_100 | combinacao
  ("no cartão"/"pago no cartão" => cartao; "pago tudo à vista"/"dinheiro" => avista.
  à vista e cartão pagam o valor inteiro — não precisam de troca nem entrada.)
- possui_troca (true/false conforme o lead disser; null se não disse)
- troca: modelo, ano, km, quitado (true/false), restante (valor aproximado)
- valor_entrada: valor que o lead dá de ENTRADA / sinal em dinheiro à vista.
  ATENÇÃO: um veículo na troca NÃO é valor_entrada. "financiar X" NÃO é entrada.
- valor_financiado: valor que o lead pretende FINANCIAR (ex.: "quero financiar 65 mil",
  "no máximo 65 mil financiado") -> valor_financiado = "65 mil" (NÃO valor_entrada).
- consorcio_contemplado (true/false se consórcio)
- interesse_agendamento (true se quer visitar/agendar; false se recusou; null se não tocou)

MULTI-INTENÇÃO (topics) — CRÍTICO:
Liste em `topics` TODOS os assuntos da MENSAGEM ATUAL do lead (pode ter vários
numa mensagem só). O funil segue normal E cada tópico é tratado no mesmo turno:
- "duvida_operacional": QUALQUER pergunta sobre processo/preço/financiamento/
  pagamento/troca/documentação/endereço/HORÁRIO DE FUNCIONAMENTO/localização/garantia.
- "agendamento": quer marcar visita ou pergunta quando pode ir.
- "ver_outros_carros": quer ver alternativas/outros modelos/mais opções.
- "pedido_foto": quer imagem/foto.
Também preencha `intent_secundario` com UM valor (compat), o mais relevante.
Exemplos:
- "É um Gol, quitado. Vocês atendem até que horas?" -> topics=["duvida_operacional"]
- "Tem outro Onix? Aceitam financiamento?"          -> topics=["ver_outros_carros","duvida_operacional"]
- "Manda fotos e o preço?"                          -> topics=["pedido_foto","duvida_operacional"]
- "Compra à vista, sem troca"                        -> topics=[]  (só resposta de funil)
NÃO repita tópico. topics=[] quando o turno é só resposta do funil.

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

INTENÇÕES IMPLÍCITAS (leia nas entrelinhas — um bom pré-atendente infere, não faz o lead repetir):
- Uma PERGUNTA sobre uma forma de negociação normalmente REVELA a intenção do lead de usá-la.
  Capture como sinal, não trate só como dúvida.
- Se o lead pergunta sobre TROCA citando um veículo/ano/modelo específico
  ("aceitam troca de veículo 2001?", "dá pra trocar meu Gol?", "meu carro é 2015, serve?",
  "troco meu Onix 2018 no negócio?") => possui_troca=true E preencha o que ele citou
  (troca.ano e/ou troca.modelo). Ele está dizendo que TEM esse veículo para dar na troca.
- "aceitam troca?" GENÉRICO, sem citar nenhum veículo/ano => possui_troca fica null
  (é só uma pergunta de política; não infira que ele tem um carro).
- "vocês financiam?" / "dá pra financiar?" / "faço no financiamento" => metodo_negociacao=financiamento.
- "aceitam consórcio?" citando carta/consórcio próprio => metodo_negociacao=consorcio.
- "consigo pagar à vista" / "pago tudo à vista" / "dinheiro" => metodo_negociacao=avista.
- "pago no cartão" / "parcelo no cartão" => metodo_negociacao=cartao.

Regras de extração:
- Amarre a interpretação à ÚLTIMA pergunta feita ao lead (use o estado atual).
- Aceite respostas não estruturadas e extraia o máximo (ex.: "Corolla 2017 quitado, dou mais 20 mil"
  -> troca: modelo=Corolla, ano=2017, quitado=true; possui_troca=true).
- "tenho X pra troca" / "dou meu carro na troca" => possui_troca=true (+ dados da troca).
- "financiamento 100%" / "sem entrada" -> metodo_negociacao = financiamento_100.
- NÃO invente DADOS que o lead não citou (não crie modelo/ano/valor do nada). Mas INFIRA
  INTENÇÃO quando o lead a revela indiretamente (ver INTENÇÕES IMPLÍCITAS acima).
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
            "Se a ÚLTIMA MENSAGEM DO LEAD responde essa pergunta — MESMO curta ou "
            "INDIRETA — preencha o campo_alvo. Afirmativa nua ('sim','isso','já "
            "está','tem') => true; número/horário => o valor. Para GATES sim/não "
            "(possui_troca, possui_entrada): negativa OU deflexão => false. Ex.: "
            "'não', 'negativo', 'só dúvida mesmo', 'era só pergunta', 'não tenho', "
            "'é meu primeiro carro', 'não é pra trocar' => campo_alvo=false. "
            "Não re-pergunte o que já foi dito/deflexionado."
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
        reasoning_effort=settings.updater_reasoning_effort,
    )
    return validate_update(update)
