"""Sprint 5 — voice composition helpers. Pure, no Agno/LLM/network.

The voice Agno agent + run_team_turn (which call the LLM) are not exercised here;
their deterministic glue (cards, contract, bubble composition, AI directive,
payload) is.
"""
from __future__ import annotations

import json

from agent.schemas import SessionState
from agent.question_planner import plan_next_question
from team.validation import constrain_offer
from team.rendering import (
    ai_identity_directive,
    build_voice_payload,
    compose_bubbles,
    presentation_contract,
    render_cards_from_decision,
)
from team.schemas import (
    BubbleSequence,
    InventoryAction,
    InventoryDecision,
    VeiculoSelecionado,
)

_INV = [
    {"external_id": "1", "brand": "Jeep", "model": "Compass", "year": 2021, "price": 120000},
    {"external_id": "2", "brand": "Chevrolet", "model": "Onix", "year": 2020, "price": 75000},
]


def _decision(action, ids):
    return InventoryDecision(
        action=action,
        veiculos_selecionados=[VeiculoSelecionado(external_id=i) for i in ids],
        motivo_geral="x",
    )


# --- render_cards_from_decision ------------------------------------------

def test_card_unico_renders_one():
    cards = render_cards_from_decision(_decision(InventoryAction.mostrar_card_unico, ["1"]), _INV)
    assert len(cards) == 1 and "Compass" in cards[0]


def test_card_lista_renders_list():
    cards = render_cards_from_decision(_decision(InventoryAction.mostrar_card_lista, ["1", "2"]), _INV)
    assert len(cards) == 1 and "Compass" in cards[0] and "Onix" in cards[0]


def test_nao_mostrar_renders_nothing():
    assert render_cards_from_decision(_decision(InventoryAction.nao_mostrar, []), _INV) == []


def test_none_decision_no_cards():
    assert render_cards_from_decision(None, _INV) == []


# --- presentation_contract (anti-lie) ------------------------------------

def test_contract_true_when_cards():
    assert presentation_contract(_decision(InventoryAction.mostrar_card_unico, ["1"])) is True


def test_contract_false_when_no_cards():
    assert presentation_contract(_decision(InventoryAction.comentar_em_texto, [])) is False
    assert presentation_contract(None) is False


# --- should_send_photos (no premature photos) ----------------------------

def test_no_photos_on_list_without_request():
    from team.validation import should_send_photos
    d = _decision(InventoryAction.mostrar_card_lista, ["1", "2"])
    d.enviar_fotos_de = ["1", "2"]
    assert should_send_photos(d, None, "quero ver opções") is False


def test_photos_on_single_vehicle():
    from team.validation import should_send_photos
    d = _decision(InventoryAction.mostrar_card_unico, ["1"])
    d.enviar_fotos_de = ["1"]
    assert should_send_photos(d, None, "quero esse") is True


def test_photos_when_lead_asks_even_on_list():
    from team.validation import should_send_photos
    d = _decision(InventoryAction.mostrar_card_lista, ["1", "2"])
    d.enviar_fotos_de = ["1", "2"]
    assert should_send_photos(d, None, "pode mandar as fotos?") is True


def test_no_photos_when_none_selected():
    from team.validation import should_send_photos
    d = _decision(InventoryAction.mostrar_card_unico, ["1"])
    d.enviar_fotos_de = []
    assert should_send_photos(d, None, "quero esse") is False


# --- constrain_offer (single closest, no auto-lists) ---------------------

def test_lead_wants_options_detection():
    from team.validation import lead_wants_options
    assert lead_wants_options(None, "tem outras opções?") is True
    assert lead_wants_options(None, "quero ver outros") is True
    assert lead_wants_options(None, "quero o Onix") is False


def test_constrain_collapses_list_to_single_prose():
    d = _decision(InventoryAction.mostrar_card_lista, ["1", "2", "3", "4"])
    out = constrain_offer(d, allow_options=False)
    assert out.action == InventoryAction.comentar_em_texto
    assert len(out.veiculos_selecionados) == 1
    assert out.veiculos_selecionados[0].external_id == "1"  # closest = first


def test_constrain_keeps_list_when_lead_asked():
    d = _decision(InventoryAction.mostrar_card_lista, ["1", "2"])
    out = constrain_offer(d, allow_options=True)
    assert out.action == InventoryAction.mostrar_card_lista
    assert len(out.veiculos_selecionados) == 2


# --- ai identity directive (Q6) ------------------------------------------

def test_ai_directive_evade_then_admit():
    assert ai_identity_directive(0) is None
    assert "desconverse" in ai_identity_directive(1).lower()
    assert "virtual" in ai_identity_directive(2).lower()


# --- compose_bubbles ------------------------------------------------------

def test_compose_order_and_cap():
    seq = BubbleSequence(
        abertura="Oi!", bolhas_extras=["b1", "b2", "b3"], fechamento="Posso ajudar?"
    )
    out = compose_bubbles(seq, cards=["[CARD]"])
    assert out == ["Oi!", "[CARD]", "b1", "b2", "Posso ajudar?"]  # extras capped at 2


def test_compose_fechamento_mandatory_even_without_abertura():
    seq = BubbleSequence(fechamento="Tchau")
    assert compose_bubbles(seq, []) == ["Tchau"]


def test_compose_drops_empty():
    seq = BubbleSequence(abertura="  ", bolhas_extras=[""], fechamento="ok")
    assert compose_bubbles(seq, []) == ["ok"]


# --- build_voice_payload --------------------------------------------------

def test_payload_contract_warning_when_no_cards():
    s = SessionState(contact_id="c1")
    nq = plan_next_question(s)
    p = json.loads(build_voice_payload(s, nq, None, [], "faq: x", None, "oi"))
    assert p["pode_falar_de_veiculos"] is False
    assert "NÃO descreva" in p["contrato_apresentacao"]
    assert p["veiculo_para_apresentar"] is None
    assert p["faq_yaml"] == "faq: x"


def test_payload_single_vehicle_for_prose():
    s = SessionState(contact_id="c1")
    nq = plan_next_question(s)
    veh = {"external_id": "1", "brand": "Jeep", "model": "Renegade", "year": 2021, "price": 83899}
    p = json.loads(
        build_voice_payload(s, nq, None, [], "", None, "oi", veiculo_destaque=veh)
    )
    assert p["pode_falar_de_veiculos"] is True
    assert p["veiculo_para_apresentar"]["modelo"] == "Renegade"
    assert "FICHA TÉCNICA" in p["contrato_apresentacao"]


def test_payload_allows_vehicles_when_cards():
    s = SessionState(contact_id="c1")
    nq = plan_next_question(s)
    dec = _decision(InventoryAction.mostrar_card_unico, ["1"])
    p = json.loads(build_voice_payload(s, nq, dec, [], "", None, "compass"))
    assert p["pode_falar_de_veiculos"] is True
