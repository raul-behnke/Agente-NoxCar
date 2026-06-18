"""Unified token/cost recording: metrics + DB audit + structured log.

record_usage() is called after every LLM call (Updater, EstoqueExpert, voice).
It computes the BRL cost, increments Prometheus counters, persists an audit row,
logs a structured line, and returns the cost.

Fase 1 / GAP-1: contact_id + conversation_id come from the structlog contextvars
bound in orchestrator.run_turn (no signature churn across the LLM seams).

v2 / CONTRATO_EVENTOS_CANONICO: each row carries a unique event_id (idempotency)
plus cost_usd/usd_brl_rate/pricing_version so the Hub reconciles cost_brl.
"""
from __future__ import annotations

import uuid
from typing import Optional

from config.settings import settings
from cost import audio_cost_breakdown, cost_breakdown
from db.usage import record_usage_row
from metrics import COST_BRL_TOTAL, TOKENS_TOTAL
from obs import log


def _ctx_ids(contact_id: Optional[str], conversation_id: Optional[str]) -> tuple:
    """Fill missing ids from the bound logging context (run_turn binds them)."""
    if contact_id and conversation_id:
        return contact_id, conversation_id
    try:
        from structlog.contextvars import get_contextvars

        ctx = get_contextvars()
        return (
            contact_id or ctx.get("contact_id"),
            conversation_id or ctx.get("conversation_id"),
        )
    except Exception:  # noqa: BLE001 - contextvars optional
        return contact_id, conversation_id


def record_usage(
    component: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None = None,
    contact_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    reasoning_tokens: int = 0,
) -> float:
    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    reasoning_tokens = int(reasoning_tokens or 0)
    total_tokens = prompt_tokens + completion_tokens
    model = model or settings.model_id
    contact_id, conversation_id = _ctx_ids(contact_id, conversation_id)
    event_id = str(uuid.uuid4())
    b = cost_breakdown(prompt_tokens, completion_tokens, model=model,
                       reasoning_tokens=reasoning_tokens)
    cost_brl = b["cost_brl"]

    TOKENS_TOTAL.labels(component, "prompt").inc(prompt_tokens)
    TOKENS_TOTAL.labels(component, "completion").inc(completion_tokens)
    COST_BRL_TOTAL.labels(component).inc(cost_brl)

    try:
        record_usage_row(
            component,
            model,
            prompt_tokens,
            completion_tokens,
            cost_brl,
            contact_id=contact_id,
            conversation_id=conversation_id,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens or None,
            event_id=event_id,
            cost_usd=b["cost_usd"],
            usd_brl_rate=b["usd_brl_rate"],
            pricing_version=b["pricing_version"],
        )
    except Exception as exc:  # noqa: BLE001 - never let accounting break a turn
        log.warning("usage_persist_failed", error=str(exc))

    log.info(
        "token_usage",
        event_id=event_id,
        component=component,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_brl=round(cost_brl, 6),
        usd_brl_rate=b["usd_brl_rate"],
        pricing_version=b["pricing_version"],
        contact_id=contact_id,
        conversation_id=conversation_id,
    )
    return cost_brl


def record_audio_usage(
    component: str,
    seconds: float,
    model: str = "whisper-1",
    contact_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> float:
    """Account Whisper transcription cost (GAP-4). Billed per minute, not tokens;
    duration is stored in total_tokens=seconds for a single audit shape."""
    seconds = float(seconds or 0.0)
    contact_id, conversation_id = _ctx_ids(contact_id, conversation_id)
    event_id = str(uuid.uuid4())
    b = audio_cost_breakdown(seconds, model=model)
    cost_brl = b["cost_brl"]

    COST_BRL_TOTAL.labels(component).inc(cost_brl)

    try:
        record_usage_row(
            component,
            model,
            0,
            0,
            cost_brl,
            contact_id=contact_id,
            conversation_id=conversation_id,
            total_tokens=int(round(seconds)),
            event_id=event_id,
            cost_usd=b["cost_usd"],
            usd_brl_rate=b["usd_brl_rate"],
            pricing_version=b["pricing_version"],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("usage_persist_failed", error=str(exc))

    log.info(
        "audio_usage",
        event_id=event_id,
        component=component,
        model=model,
        seconds=round(seconds, 2),
        cost_brl=round(cost_brl, 6),
        usd_brl_rate=b["usd_brl_rate"],
        pricing_version=b["pricing_version"],
        contact_id=contact_id,
        conversation_id=conversation_id,
    )
    return cost_brl
