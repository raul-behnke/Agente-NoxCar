"""Sprint 4 — inventory + EstoqueExpert decision logic. Pure, no Agno/LLM/network.

The Agno agent itself (team/inventory_expert.build_inventory_expert) is not run
here; its deterministic guards (detect/validate) and the Python data layer are.
"""
from __future__ import annotations

from agent.schemas import Collected, SessionState, StateUpdate
from agent.templates import render_vehicle_card, render_vehicle_list
from team.schemas import InventoryAction, InventoryDecision, VeiculoSelecionado
from team.validation import detect_inventory_signal, validate_inventory_decision
from tools import inventory as inv


_RAW = [
    {"id": 1, "marca": "Jeep", "modelo": "Compass", "ano": 2021, "preco": 120000,
     "km": 30000, "categoria": "SUV", "fotos": ["a.jpg", "b.jpg"]},
    {"external_id": "x2", "brand": "Chevrolet", "model": "Onix", "year": 2020,
     "price": 75000, "mileage": 40000, "category": "Hatch"},
]


def _norm():
    return [inv._normalize_vehicle(r) for r in _RAW]


# --- normalization (pt/en heterogeneity) ---------------------------------

def test_normalize_pt_and_en_keys():
    n = _norm()
    assert n[0]["external_id"] == "1" and n[0]["brand"] == "Jeep"
    assert n[0]["price"] == 120000 and n[0]["fotos"] == ["a.jpg", "b.jpg"]
    assert n[1]["external_id"] == "x2" and n[1]["model"] == "Onix"
    assert n[1]["km"] == 40000  # mileage alias


def test_snapshot_is_compact_one_line_each():
    snap = inv.format_inventory_snapshot(_norm())
    assert snap.count("\n") == 1
    assert "Compass" in snap and "x2" in snap


def test_prefilter_is_noop_v1():
    n = _norm()
    assert inv.prefilter_inventory(n) == n


# --- detect_inventory_signal ---------------------------------------------

def _state(**c):
    s = SessionState(contact_id="c1")
    for k, v in c.items():
        setattr(s.collected, k, v)
    return s


def test_signal_on_vehicle_of_interest():
    assert detect_inventory_signal(_state(veiculo_interesse="Compass"), None) is True


def test_signal_suppressed_when_already_shown():
    s = _state(veiculo_interesse="Compass")
    s.vehicles_shown = ["Compass"]
    assert detect_inventory_signal(s, None, "ok obrigado") is False


def test_signal_on_intent():
    assert detect_inventory_signal(_state(), StateUpdate(intent="pedido_foto")) is True


def test_signal_on_keyword():
    assert detect_inventory_signal(_state(), None, "tem algum suv?") is True


def test_signal_not_refired_after_shown_on_faq_question():
    # vehicle already presented; lead asks a financing FAQ doubt -> do NOT re-show
    s = _state(veiculo_interesse="Jeep Renegade LNGTD AT 19/20")
    s.vehicles_shown = ["4830870"]  # already shown (by id)
    assert detect_inventory_signal(s, None, "quanto de entrada preciso dar nesse?") is False


def test_signal_suppressed_during_troca_collection():
    # lead describes THEIR trade-in car (km/ano/modelo) -> NOT an inventory query;
    # must not re-present the interest vehicle
    from agent.schemas import Collected, TrocaInfo
    s = _state(veiculo_interesse="Peugeot 208")
    s.vehicles_shown = ["123"]
    s.collected.possui_troca = True  # mid trade-in collection
    upd = StateUpdate(collected=Collected(troca=TrocaInfo(modelo="Gol", km="280000")))
    assert detect_inventory_signal(s, upd, "é um Gol, tá com 280km, quitado") is False


# --- validate_inventory_decision (anti-hallucination + degrade) ----------

def test_validate_filters_hallucinated_ids():
    d = InventoryDecision(
        action=InventoryAction.mostrar_card_lista,
        veiculos_selecionados=[VeiculoSelecionado(external_id="1"),
                               VeiculoSelecionado(external_id="ghost")],
        enviar_fotos_de=["1", "ghost"],
        motivo_geral="x",
    )
    out = validate_inventory_decision(d, _norm())
    ids = [v.external_id for v in out.veiculos_selecionados]
    assert ids == ["1"]
    assert out.enviar_fotos_de == ["1"]


def test_validate_no_valid_degrades_to_nao_mostrar():
    d = InventoryDecision(
        action=InventoryAction.mostrar_card_unico,
        veiculos_selecionados=[VeiculoSelecionado(external_id="ghost")],
        motivo_geral="x",
    )
    out = validate_inventory_decision(d, _norm())
    assert out.action == InventoryAction.nao_mostrar


def test_validate_unico_with_many_becomes_lista():
    d = InventoryDecision(
        action=InventoryAction.mostrar_card_unico,
        veiculos_selecionados=[VeiculoSelecionado(external_id="1"),
                               VeiculoSelecionado(external_id="x2")],
        motivo_geral="x",
    )
    assert validate_inventory_decision(d, _norm()).action == InventoryAction.mostrar_card_lista


def test_validate_lista_with_one_becomes_unico():
    d = InventoryDecision(
        action=InventoryAction.mostrar_card_lista,
        veiculos_selecionados=[VeiculoSelecionado(external_id="1")],
        motivo_geral="x",
    )
    assert validate_inventory_decision(d, _norm()).action == InventoryAction.mostrar_card_unico


# --- templates (no invention) --------------------------------------------

def test_render_card_only_present_fields():
    card = render_vehicle_card({"brand": "Jeep", "model": "Compass", "year": 2021})
    assert "Compass" in card and "Ano: 2021" in card
    assert "Valor" not in card  # price absent -> not rendered (no invention)


def test_render_list_formats_each():
    out = render_vehicle_list(_norm())
    assert "Compass" in out and "Onix" in out
    assert out.count("\n") == 1
