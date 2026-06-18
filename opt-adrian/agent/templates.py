"""Deterministic vehicle cards (reference parity: agent/templates.py).

Cards are rendered in Python (visual consistency + token economy), NOT by the
LLM. Only fields actually present are rendered — never invent (PRD §4.10/§7.1.4).
"""
from __future__ import annotations

from typing import Any


def _fmt_price(p) -> str:
    try:
        return f"R$ {int(float(p)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return None


def render_vehicle_card(v: dict[str, Any]) -> str:
    desc = " ".join(
        str(x) for x in (v.get("brand"), v.get("model"), v.get("version")) if x
    )
    lines = [f"🚗 {desc}".strip()]
    if v.get("year"):
        lines.append(f"Ano: {v['year']}")
    if v.get("km") not in (None, ""):
        lines.append(f"KM: {v['km']}")
    price = _fmt_price(v.get("price"))
    if price:
        lines.append(f"Valor: {price}")
    return "\n".join(lines)


def render_vehicle_list(vehicles: list[dict[str, Any]]) -> str:
    parts = []
    for v in vehicles:
        desc = " ".join(
            str(x) for x in (v.get("brand"), v.get("model"), v.get("version")) if x
        )
        price = _fmt_price(v.get("price"))
        line = f"• {desc}".rstrip()
        if v.get("year"):
            line += f" {v['year']}"
        if price:
            line += f" — {price}"
        parts.append(line)
    return "\n".join(parts)
