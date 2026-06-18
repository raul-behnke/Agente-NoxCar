"""Token-usage persistence (audit trail of tokens + BRL cost per call).

Fase 1 / GAP-1: rows carry contact_id + conversation_id so cost is attributable
per conversation/lead. total_tokens (GAP-3) is stored denormalized.

v2 / CONTRATO_EVENTOS_CANONICO: each row is a canonical LLM_CALL (or
WHISPER_TRANSCRIPTION) event. It carries event_id (idempotency), cost_usd,
usd_brl_rate and pricing_version for Hub reconciliation; usage_since() serializes
the full envelope.
"""
from __future__ import annotations

from typing import Optional

from config.settings import settings
from db.engine import connect


def record_usage_row(
    component: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_brl: float,
    contact_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    total_tokens: Optional[int] = None,
    reasoning_tokens: Optional[int] = None,
    event_id: Optional[str] = None,
    cost_usd: Optional[float] = None,
    usd_brl_rate: Optional[float] = None,
    pricing_version: Optional[str] = None,
) -> None:
    if total_tokens is None:
        total_tokens = int(prompt_tokens) + int(completion_tokens)
    with connect() as con:
        con.execute(
            "INSERT INTO token_usage("
            "component, model, prompt_tokens, completion_tokens, total_tokens, "
            "reasoning_tokens, cost_brl, cost_usd, usd_brl_rate, pricing_version, "
            "event_id, contact_id, conversation_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                component,
                model,
                int(prompt_tokens),
                int(completion_tokens),
                int(total_tokens),
                int(reasoning_tokens) if reasoning_tokens is not None else None,
                float(cost_brl),
                float(cost_usd) if cost_usd is not None else None,
                float(usd_brl_rate) if usd_brl_rate is not None else None,
                pricing_version,
                event_id,
                contact_id,
                conversation_id,
            ),
        )


def usage_totals() -> dict:
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
            "COALESCE(SUM(cost_brl),0.0) FROM token_usage"
        ).fetchone()
    return {
        "prompt_tokens": row[0],
        "completion_tokens": row[1],
        "total_tokens": row[0] + row[1],
        "cost_brl": round(row[2], 4),
    }


def usage_by_contact(contact_id: str) -> dict:
    """Cost + tokens attributable to a single contact/lead (GAP-1 payoff)."""
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
            "COALESCE(SUM(cost_brl),0.0), COUNT(*) FROM token_usage WHERE contact_id = ?",
            (contact_id,),
        ).fetchone()
    return {
        "contact_id": contact_id,
        "prompt_tokens": row[0],
        "completion_tokens": row[1],
        "total_tokens": row[0] + row[1],
        "cost_brl": round(row[2], 4),
        "calls": row[3],
    }


def usage_since(since_id: int = 0, limit: int = 1000) -> list[dict]:
    """Incremental pull of cost rows AS canonical events (CONTRATO §3.1/§3.2).

    Each token_usage row becomes a LLM_CALL (or WHISPER_TRANSCRIPTION) event with
    the full envelope so the Hub dedups by event_id and reconciles cost_brl.
    """
    with connect() as con:
        rows = con.execute(
            "SELECT id, component, model, prompt_tokens, completion_tokens, "
            "total_tokens, reasoning_tokens, cost_brl, cost_usd, usd_brl_rate, "
            "pricing_version, event_id, contact_id, conversation_id, created_at "
            "FROM token_usage WHERE id > ? ORDER BY id ASC LIMIT ?",
            (int(since_id), int(limit)),
        ).fetchall()
    out = []
    for r in rows:
        is_audio = r["component"] == "whisper"
        if is_audio:
            payload = {
                "model": r["model"],
                "audio_seconds": r["total_tokens"],
                "cost_brl": r["cost_brl"],
                "cost_usd": r["cost_usd"],
                "usd_brl_rate": r["usd_brl_rate"],
                "pricing_version": r["pricing_version"],
            }
            event_type = "WHISPER_TRANSCRIPTION"
        else:
            payload = {
                "component": r["component"],
                "model": r["model"],
                "input_tokens": r["prompt_tokens"],
                "output_tokens": r["completion_tokens"],
                "total_tokens": r["total_tokens"],
                "reasoning_tokens": r["reasoning_tokens"],
                "cost_brl": r["cost_brl"],
                "cost_usd": r["cost_usd"],
                "usd_brl_rate": r["usd_brl_rate"],
                "pricing_version": r["pricing_version"],
            }
            event_type = "LLM_CALL"
        out.append(
            {
                "cursor_id": r["id"],
                "event_id": r["event_id"],
                "schema_version": 1,
                "event_type": event_type,
                "client": settings.client_slug,
                "agent": settings.agent_slug,
                "contact_id": r["contact_id"],
                "conversation_id": r["conversation_id"],
                "occurred_at": r["created_at"],
                "payload": payload,
            }
        )
    return out


def get_price(model: str | None, kind: str) -> Optional[float]:
    """Resolve a confirmed unit price (USD) from the pricing table.

    Returns None when no row matches so callers fall back to env defaults (GAP-2).
    """
    row = get_pricing(model, kind)
    return row["price_usd"] if row else None


def get_pricing(model: str | None, kind: str) -> Optional[dict]:
    """Latest confirmed pricing row for (model, kind): price + rate + version."""
    if not model:
        return None
    try:
        with connect() as con:
            row = con.execute(
                "SELECT price_usd, usd_brl_rate, pricing_version FROM pricing "
                "WHERE model = ? AND kind = ? ORDER BY effective_from DESC LIMIT 1",
                (model, kind),
            ).fetchone()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001 - pricing table optional; never break costing
        return None
