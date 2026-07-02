"""Pure composition helpers for the voice turn (no Agno/LLM — testable offline).

Splits the deterministic parts of run_team_turn out of the Agno layer:
- render_cards_from_decision: InventoryDecision -> Python card strings
- presentation_contract: may the voice promise vehicles this turn? (anti-lie)
- ai_identity_directive: evade 1st / admit 2nd (grill Q6)
- compose_bubbles: [abertura?] + [cards?] + [bolhas_extras<=2] + [fechamento]
- build_voice_payload: the JSON-ish context block fed to the voice agent
"""
from __future__ import annotations

import json
from typing import Optional

from agent.schemas import SessionState
from agent.templates import render_vehicle_card, render_vehicle_list
from config.settings import settings
from team.schemas import BubbleSequence, InventoryAction, InventoryDecision


def _acknowledge_hint(state: SessionState, last_message: str) -> Optional[dict]:
    """Dica determinística do que reconhecer com naturalidade (paridade AMC).
    Evita microrreação forçada: só sugere reconhecer o que o lead ACABOU de dar."""
    ack: dict = {}
    low = (last_message or "").lower()
    c = state.collected
    if c.nome and c.nome.lower() in low and len(low) < 40:
        ack["acabou_de_dar_nome"] = c.nome
    if c.troca.quitado is False and ("financ" in low or "quitad" in low or "saldo" in low):
        ack["situacao_troca"] = "troca ainda não quitada"
    if c.cidade and c.cidade.lower() in low:
        ack["mencionou_cidade"] = c.cidade
    return ack or None


def _format_business_hours() -> str:
    """Horário de atendimento (informativo, p/ a voice responder 'até que horas
    atendem?' sem inventar). Apenas informação — NÃO controla modo do agente."""
    return "segunda a sexta: 08:00–18:30; sábado: 09:00–13:00; domingo: fechado"


def _vehicles_from_decision(decision: InventoryDecision, inventory: list[dict]) -> list[dict]:
    by_id = {str(v.get("external_id")): v for v in inventory}
    return [by_id[s.external_id] for s in decision.veiculos_selecionados if s.external_id in by_id]


def render_cards_from_decision(
    decision: Optional[InventoryDecision], inventory: list[dict]
) -> list[str]:
    if decision is None:
        return []
    vehicles = _vehicles_from_decision(decision, inventory)
    if decision.action == InventoryAction.mostrar_card_unico and vehicles:
        return [render_vehicle_card(vehicles[0])]
    if decision.action == InventoryAction.mostrar_card_lista and vehicles:
        return [render_vehicle_list(vehicles)]
    return []


def presentation_contract(decision: Optional[InventoryDecision]) -> bool:
    """True if real cards will be shown -> voice MAY reference vehicles. False ->
    voice must NOT promise/describe specific vehicles (anti-lie contract, PRD §4.10)."""
    if decision is None:
        return False
    return decision.action in (
        InventoryAction.mostrar_card_unico,
        InventoryAction.mostrar_card_lista,
    ) and bool(decision.veiculos_selecionados)


def ai_identity_directive(count: int) -> Optional[str]:
    """grill Q6: evade on the 1st ask, admit on the 2nd."""
    if count <= 0:
        return None
    if count == 1:
        return "O lead perguntou se você é IA pela 1ª vez: desconverse com leveza, sem confirmar nem negar."
    return "O lead já perguntou se você é IA: assuma com naturalidade que é um atendimento virtual da loja."


def compose_bubbles(seq: BubbleSequence, cards: list[str]) -> list[str]:
    """Final bubble order. `fechamento` is mandatory; extras capped at 2."""
    bubbles: list[str] = []
    if seq.abertura:
        bubbles.append(seq.abertura)
    bubbles.extend(cards)
    bubbles.extend(seq.bolhas_extras[:2])
    bubbles.append(seq.fechamento)
    return [b for b in bubbles if b and b.strip()]


def build_voice_payload(
    state: SessionState,
    next_question,
    decision: Optional[InventoryDecision],
    cards: list[str],
    faq_yaml: str,
    ai_directive: Optional[str],
    last_message: str,
    photos: Optional[list[str]] = None,
    veiculo_destaque: Optional[dict] = None,
    veiculos_opcoes: Optional[list[dict]] = None,
    veiculo_em_foco: Optional[dict] = None,
    fotos_indisponiveis: bool = False,
) -> str:
    fotos_serao_enviadas = bool(photos)

    def _money(p):
        try:
            return f"R$ {int(float(p)):,}".replace(",", ".")
        except (TypeError, ValueError):
            return None

    def _km(k):
        try:
            return f"{int(float(k)):,}".replace(",", ".") + " km"
        except (TypeError, ValueError):
            return None

    def _slim(v: dict) -> dict:
        return {
            "marca": v.get("brand"), "modelo": v.get("model"), "versao": v.get("version"),
            "ano": v.get("year"), "preco": _money(v.get("price")), "km": _km(v.get("km")),
            "categoria": v.get("category"), "cambio": v.get("cambio"),
            "combustivel": v.get("combustivel"), "cor": v.get("cor"),
            "motivo": v.get("motivo"),  # sales angle (motivo_individual)
        }

    def _ficha(v: dict) -> dict:
        # full data for the highlighted vehicle (ficha técnica + attribute answers);
        # only fields actually present — the voice must NOT invent anything beyond this.
        d = _slim(v)
        d["portas"] = v.get("portas")
        d["opcionais"] = v.get("opcionais") or []
        return {k: val for k, val in d.items() if val not in (None, "", [])}

    destaque = _ficha(veiculo_destaque) if veiculo_destaque else None
    opcoes = [_slim(v) for v in (veiculos_opcoes or [])]
    # The voice may describe a vehicle only if there is a real one (highlighted or carded).
    pode_falar = bool(destaque) or presentation_contract(decision)
    payload = {
        "ultima_mensagem_lead": last_message,
        "intent_do_turno": getattr(next_question, "intent", None)
        and next_question.intent.value,
        "pergunta_alvo": getattr(next_question, "canonical_text", None),
        "estado_coletado": json.loads(state.collected.model_dump_json()),
        "ja_saudou": state.saudacao_feita,
        "contrato_saudacao": (
            "JÁ houve saudação antes — NÃO se apresente de novo, NÃO repita 'Olá, aqui é o Adrian', "
            "NÃO pergunte o nome de novo se já perguntou. Continue a conversa do ponto atual."
            if state.saudacao_feita
            else (
                "Primeiro contato. Abra assim (adapte as palavras, mantenha o sentido):\n"
                "1) 'Olá! Aqui é o Adrian, da NOXCAR. Tudo bem? 😊'\n"
                "2) Anuncie o vídeo da estrutura: 'Já te mando um vídeo mostrando a estrutura "
                "da nossa loja e o nosso jeito de trabalhar 👇' (o vídeo JÁ está sendo enviado — "
                "não descreva, só anuncie).\n"
                "3) Termine com a 'pergunta_alvo' (reconhecer/confirmar o veículo ou pedir o nome)."
            )
        ),
        "veiculo_para_apresentar": destaque,  # SINGLE closest match (prose, no bullets)
        "veiculos_opcoes": opcoes,  # only when lead asked for options (prose, max 3)
        "pode_falar_de_veiculos": pode_falar,
        "contrato_apresentacao": (
            (
                "O lead pediu opções: use o FORMATO LISTA com até 3 de 'veiculos_opcoes' "
                "(1 por linha: modelo versao ano — R$ preço) e pergunte se quer a ficha de alguma."
                if opcoes
                else "Apresente o 'veiculo_para_apresentar' no FORMATO FICHA TÉCNICA. "
                "Se o lead perguntou um atributo específico, responda só aquele dado. Não cite outros veículos."
            )
            if destaque
            else "NÃO descreva veículos específicos neste turno (não há veículo definido)."
        ),
        "fotos_serao_enviadas": fotos_serao_enviadas,
        "fotos_indisponiveis": fotos_indisponiveis,
        "contrato_fotos": (
            "As fotos desse veículo ainda NÃO estão cadastradas no sistema no momento. "
            "Avise o lead com naturalidade que as fotos ainda não estão disponíveis, "
            "mas que você já vai pedir para o consultor enviar. NÃO prometa mandar agora."
            if fotos_indisponiveis
            else "Fotos SERÃO enviadas; pode mencioná-las."
            if fotos_serao_enviadas
            else "NÃO há fotos disponíveis; NÃO prometa enviar fotos."
        ),
        "hint_estoque": decision.hint_narrativo if decision else None,
        "diretiva_identidade_ia": ai_directive,
        # veículo EM FOCO (ficha completa do último mostrado) — verdade p/ atributos
        "veiculo_em_foco": _ficha(veiculo_em_foco) if veiculo_em_foco else None,
        "acknowledge_hint": _acknowledge_hint(state, last_message),
        # o lead ACABOU de confirmar o veículo e vamos começar a qualificação ->
        # a voice faz a ponte calorosa antes da 1ª pergunta do funil.
        "iniciar_qualificacao": (
            state.collected.veiculo_interesse_confirmado is True
            and not state.collected.nome
            and getattr(next_question, "intent", None)
            and next_question.intent.value == "funil"
        ),
        "horario_funcionamento": _format_business_hours(),
        "contrato_duvida": (
            "Se a mensagem do lead contém uma PERGUNTA (ex.: horário de atendimento, "
            "formas de pagamento, localização), RESPONDA-A de forma curta ANTES da "
            "pergunta_alvo — use faq_yaml e horario_funcionamento; não invente. "
            "Depois de responder, faça a pergunta_alvo NO MESMO turno. Se não souber, "
            "diga que o consultor confirma e siga com a pergunta_alvo."
        ),
        "faq_yaml": faq_yaml,
    }
    return json.dumps(payload, ensure_ascii=False)
