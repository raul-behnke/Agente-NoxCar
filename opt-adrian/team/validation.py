"""Deterministic inventory gating + decision validation (reference parity:
detect_inventory_signal + _validate_inventory_decision). PURE — no Agno/LLM.

- detect_inventory_signal: decides WHEN to call the EstoqueExpert (cheap, code).
- validate_inventory_decision: blindly filters hallucinated external_ids and
  degrades the action gracefully (anti-hallucination, PRD §4.10).
"""
from __future__ import annotations

from typing import Optional

from agent.schemas import SessionState, StateUpdate
from team.schemas import InventoryAction, InventoryDecision

_INVENTORY_INTENTS = {"apresentar", "ver_outros_carros", "pedido_foto"}
_VEHICLE_KEYWORDS = {
    "carro", "veiculo", "veículo", "modelo", "suv", "sedan", "hatch", "picape",
    "caminhonete", "foto", "fotos", "preço", "preco", "valor", "ano", "km",
}


def detect_inventory_signal(
    state: SessionState, update: Optional[StateUpdate], last_message: str = ""
) -> bool:
    """True when the turn needs the EstoqueExpert (otherwise the voice agent
    conducts the funnel alone)."""
    c = state.collected
    if update and update.intent in _INVENTORY_INTENTS:
        return True
    # multi-intenção (paridade AMC): pedido de outros modelos / foto num turno
    # que também é resposta de funil -> ainda aciona o EstoqueExpert.
    if update is not None:
        topics = set(update.topics or [])
        if update.intent_secundario in ("ver_outros_carros", "pedido_foto") or (
            topics & {"ver_outros_carros", "pedido_foto"}
        ):
            return True
    # FIRST presentation only: interest set and nothing shown yet. (Do NOT compare
    # veiculo_interesse — a model NAME — against vehicles_shown, which holds IDs;
    # that never matched and made the ficha re-fire every turn.)
    if c.veiculo_interesse and not state.vehicles_shown:
        return True
    # TRADE-IN collection: the lead is describing THEIR car (modelo/ano/km/quitado).
    # Those vehicle keywords are NOT an inventory query — don't re-present the
    # interest vehicle. Explicit asks ('ver outros', 'manda foto') were already
    # handled above via update.intent, so this only kills the fuzzy keyword match.
    collecting_troca = (
        update is not None and (
            update.collected.possui_troca is True
            or any(getattr(update.collected.troca, s) is not None
                   for s in ("modelo", "ano", "km", "quitado"))
        )
    ) or (c.possui_troca is True and not c.troca.is_complete())
    if collecting_troca:
        return False
    # explicit vehicle/spec keyword in this message (attribute/options/photos)
    msg = (last_message or "").lower()
    if any(kw in msg for kw in _VEHICLE_KEYWORDS):
        return True
    return False


_PHOTO_REQUEST_WORDS = ("foto", "fotos", "imagem", "imagens", "fotinha", "manda foto")

_OPTION_REQUEST_WORDS = (
    "outro", "outros", "outra", "outras", "opç", "opc", "alternativ",
    "mais carro", "mais veic", "mais opç", "mais opc", "ver todos", "ver todas",
    "que carros", "quais carros", "que veiculos", "ver a lista", "mais modelo",
)  # NOTE: avoid bare 'tem mais'/'mostra mais' — they match 'tem mais fotos'


def lead_wants_options(update, last_message: str = "") -> bool:
    """True only when the lead explicitly asks to see more/other vehicles. Until
    then we present a SINGLE closest match and qualify first (no auto-lists).

    Multi-intenção (paridade AMC): reconhece o pedido de opções também por
    intent_secundario/topics, não só pela intent primária ou por palavra-chave —
    assim o agente APRESENTA LISTA de modelos quando o lead pede."""
    if update is not None:
        if getattr(update, "intent", None) in ("ver_outros_carros", "apresentar"):
            return True
        if getattr(update, "intent_secundario", None) == "ver_outros_carros":
            return True
        if "ver_outros_carros" in (getattr(update, "topics", None) or []):
            return True
    return bool(last_message) and any(
        w in last_message.lower() for w in _OPTION_REQUEST_WORDS
    )


def constrain_offer(decision, allow_options: bool):
    """Business rule: never auto-present a list. Collapse to the single closest
    vehicle and speak it in prose (no bullet cards) unless the lead asked for
    options."""
    if decision is None or allow_options or not decision.veiculos_selecionados:
        return decision
    d = decision.model_copy(deep=True)
    d.veiculos_selecionados = d.veiculos_selecionados[:1]
    d.action = InventoryAction.comentar_em_texto  # prose, no bullet list
    return d


def should_send_photos(decision, update, last_message: str = "") -> bool:
    """Gate photo sending (business rule): only when a SINGLE vehicle is focused
    OR the lead explicitly asked for photos. Never blast photos on a multi-option
    list before the lead picks a vehicle."""
    if not decision or not decision.enviar_fotos_de:
        return False
    if update is not None and getattr(update, "intent", None) == "pedido_foto":
        return True
    if last_message and any(w in last_message.lower() for w in _PHOTO_REQUEST_WORDS):
        return True
    return decision.action == InventoryAction.mostrar_card_unico


def validate_inventory_decision(
    decision: InventoryDecision, inventory: list[dict]
) -> InventoryDecision:
    """Filter hallucinated ids and degrade the action so it never references
    vehicles that don't exist."""
    valid_ids = {str(v.get("external_id")) for v in inventory if v.get("external_id")}

    d = decision.model_copy(deep=True)
    d.veiculos_selecionados = [
        v for v in d.veiculos_selecionados if v.external_id in valid_ids
    ]
    d.enviar_fotos_de = [fid for fid in d.enviar_fotos_de if fid in valid_ids]
    n = len(d.veiculos_selecionados)

    if d.action in (InventoryAction.mostrar_card_unico, InventoryAction.mostrar_card_lista):
        if n == 0:
            d.action = InventoryAction.nao_mostrar
        elif d.action == InventoryAction.mostrar_card_unico and n > 1:
            d.action = InventoryAction.mostrar_card_lista
        elif d.action == InventoryAction.mostrar_card_lista and n == 1:
            d.action = InventoryAction.mostrar_card_unico
    return d
