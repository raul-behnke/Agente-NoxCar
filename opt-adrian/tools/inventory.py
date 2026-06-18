"""Inventory access + normalization (reference parity: tools/inventory.py).

Stock is the live JSON Custom Value (cached 5min in ghl/custom_values). This
module normalizes the heterogeneous CRM schema (pt/en keys), builds the compact
snapshot injected into the EstoqueExpert prompt (~60 vehicles -> whole catalog,
grill Q5), and exposes a `prefilter_inventory` seam (no-op in V1).

NO Agno import — pure data, testable offline.
"""
from __future__ import annotations

from typing import Any

from ghl.client import crm

# normalized key -> possible raw keys (pt/en heterogeneity, PRD risk §14.6)
_FIELD_ALIASES = {
    "external_id": ("external_id", "id", "vehicle_id", "codigo"),
    "brand": ("brand", "marca", "make"),
    "model": ("model", "modelo"),
    "version": ("version", "versao", "versão", "trim"),
    "year": ("year", "ano", "ano_modelo", "ano_fabricacao"),
    "price": ("price", "preco", "preço", "valor"),
    "km": ("km", "quilometragem", "mileage", "kms"),
    "category": ("category", "categoria", "segmento", "carroceria", "tipo_veiculo"),
    "cambio": ("cambio", "câmbio", "transmissao", "transmissão", "transmission"),
    "combustivel": ("combustivel", "combustível", "fuel"),
    "cor": ("cor", "color", "colour"),
    "portas": ("portas", "doors"),
    "descricao": ("descricao", "descrição", "description"),
    "opcionais": ("opcionais", "options", "acessorios", "acessórios"),
    "fotos": ("fotos", "imagens", "photos", "images"),
}


def _pick(raw: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


def _normalize_opcionais(raw) -> list[str]:
    """Opcionais may be a list of strings or of {Descricao: ...} objects."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            d = item.get("Descricao") or item.get("descricao") or item.get("nome")
            if d:
                out.append(str(d).strip())
    return out


def _normalize_vehicle(raw: dict[str, Any]) -> dict[str, Any]:
    norm = {field: _pick(raw, aliases) for field, aliases in _FIELD_ALIASES.items()}
    if norm.get("external_id") is not None:
        norm["external_id"] = str(norm["external_id"])
    if not isinstance(norm.get("fotos"), list):
        norm["fotos"] = [norm["fotos"]] if norm.get("fotos") else []
    norm["opcionais"] = _normalize_opcionais(norm.get("opcionais"))
    return norm


def load_inventory() -> list[dict[str, Any]]:
    """Normalized stock list (raw cached 5min upstream in ghl.custom_values)."""
    return [_normalize_vehicle(v) for v in crm.get_stock()]


def get_vehicle_details(external_id: str) -> dict[str, Any]:
    """Full normalized record for one vehicle (anti-hallucination of specs)."""
    for v in load_inventory():
        if v.get("external_id") == str(external_id):
            return v
    return {}


def prefilter_inventory(
    inventory: list[dict], collected: Any | None = None
) -> list[dict]:
    """Seam for deterministic pre-filtering (grill Q5). No-op in V1 (~60 vehicles
    fit whole-in-prompt). Enable by brand/model/category/price when the catalog grows."""
    return inventory


def _fmt_price(p) -> str:
    try:
        return f"R${int(float(p)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(p) if p else "-"


def format_inventory_snapshot(inventory: list[dict]) -> str:
    """Compact one-line-per-vehicle snapshot for the EstoqueExpert prompt."""
    lines = []
    for v in inventory:
        desc = " ".join(
            str(x) for x in (v.get("brand"), v.get("model"), v.get("version")) if x
        )
        n_fotos = len(v.get("fotos") or [])
        fotos_flag = f"fotos:{n_fotos}" if n_fotos else "sem_foto"
        opc = ", ".join(str(o) for o in (v.get("opcionais") or [])[:3]) or "-"
        lines.append(
            f"{v.get('external_id')}|{desc}|{v.get('year') or '-'}|"
            f"{_fmt_price(v.get('price'))}|{v.get('km') or '-'}km|"
            f"{v.get('category') or '-'}|{v.get('cambio') or '-'}|"
            f"{v.get('combustivel') or '-'}|{v.get('cor') or '-'}|{opc}|{fotos_flag}"
        )
    return "\n".join(lines)
