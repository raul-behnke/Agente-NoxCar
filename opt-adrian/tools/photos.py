"""Vehicle photo resolution + send (reference parity: tools/photos.py).

Resolves photo URLs from the (normalized) inventory by external_id and sends them
via the CRM. Pure resolution is testable offline; the async send takes the CRM
client as an argument so it stays mockable.
"""
from __future__ import annotations

import asyncio
from typing import Any


def _photo_url(item) -> str | None:
    """Extract a URL from a photo entry — handles plain strings and the common
    object shapes ({url|link|src|imagem|image: ...})."""
    if isinstance(item, str):
        return item.strip() or None
    if isinstance(item, dict):
        for k in ("url", "link", "src", "imagem", "image", "href"):
            val = item.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def build_photo_payload_by_id(external_id: str, inventory: list[dict]) -> list[str]:
    """Return the photo URLs for one vehicle (empty if none / not found).

    Tolerant of feed shape: `fotos` may be a list of URL strings or of objects.
    """
    for v in inventory:
        if str(v.get("external_id")) == str(external_id):
            return [u for u in (_photo_url(x) for x in (v.get("fotos") or [])) if u]
    return []


def resolve_photos(
    external_ids: list[str],
    inventory: list[dict],
    per_vehicle: int | None = None,
    total: int | None = None,
) -> list[str]:
    """Flatten photo URLs for several vehicles (order preserved, deduped).

    `per_vehicle` caps photos taken from each vehicle; `total` caps the whole
    batch — vehicles routinely carry 14+ photos, which would spam WhatsApp.
    """
    urls: list[str] = []
    for eid in external_ids:
        vp = build_photo_payload_by_id(eid, inventory)
        if per_vehicle is not None:
            vp = vp[:per_vehicle]
        for u in vp:
            if u not in urls:
                urls.append(u)
            if total is not None and len(urls) >= total:
                return urls
    return urls


async def send_photos(crm, conversation_id: str, urls: list[str]) -> None:
    """Send photos concurrently (best-effort; failures don't block the turn)."""
    async def _one(url: str) -> None:
        try:
            await asyncio.to_thread(crm.send_attachment, conversation_id, url)
        except Exception:  # noqa: BLE001
            pass

    if urls:
        await asyncio.gather(*(_one(u) for u in urls))
