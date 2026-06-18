"""Token-cost computation (pure, testable). Converts token usage to BRL.

Price resolution order (GAP-2):
  1. explicit price_*_per_1m argument (used by tests / overrides)
  2. confirmed `pricing` table row for the model (db.usage.get_pricing)
  3. settings/env default (PLACEHOLDER until confirmed with OpenAI billing)
USD->BRL conversion (usd_brl_rate) is always applied last.

v2 / CONTRATO_EVENTOS_CANONICO §3.1/§4: cost_breakdown() returns cost_usd,
cost_brl, usd_brl_rate and pricing_version so each LLM_CALL/WHISPER event the Hub
ingests can be reconciled against the central pricing.
"""
from __future__ import annotations

import math

from config.settings import settings

# pricing_version tag used when no confirmed `pricing` row exists (env fallback).
PLACEHOLDER_VERSION = "placeholder-env"


def _resolve(model: str | None, kind: str, env_default: float) -> float:
    from db.usage import get_price

    price = get_price(model, kind)
    return env_default if price is None else price


def compute_cost_usd(
    prompt_tokens: int,
    completion_tokens: int,
    price_in_per_1m: float | None = None,
    price_out_per_1m: float | None = None,
    model: str | None = None,
) -> float:
    pin = (
        _resolve(model, "input", settings.price_input_usd_per_1m)
        if price_in_per_1m is None
        else price_in_per_1m
    )
    pout = (
        _resolve(model, "output", settings.price_output_usd_per_1m)
        if price_out_per_1m is None
        else price_out_per_1m
    )
    return (prompt_tokens / 1_000_000) * pin + (completion_tokens / 1_000_000) * pout


def compute_cost_brl(
    prompt_tokens: int,
    completion_tokens: int,
    rate: float | None = None,
    **kwargs,
) -> float:
    usd = compute_cost_usd(prompt_tokens, completion_tokens, **kwargs)
    return usd * (settings.usd_brl_rate if rate is None else rate)


def compute_audio_cost_brl(
    seconds: float,
    model: str | None = None,
    rate: float | None = None,
) -> float:
    """Whisper bills per audio minute (GAP-4), rounded up (CONTRATO §4)."""
    from db.usage import get_price

    price_per_min = get_price(model, "audio_minute")
    if price_per_min is None:
        price_per_min = settings.whisper_price_usd_per_min
    usd = math.ceil(seconds / 60.0) * price_per_min
    return usd * (settings.usd_brl_rate if rate is None else rate)


def _pricing_meta(model: str | None, kind: str) -> tuple[float | None, float | None, str | None]:
    """(price_usd, usd_brl_rate, pricing_version) from the confirmed pricing row."""
    from db.usage import get_pricing

    row = get_pricing(model, kind)
    if not row:
        return None, None, None
    return row.get("price_usd"), row.get("usd_brl_rate"), row.get("pricing_version")


def cost_breakdown(
    input_tokens: int,
    output_tokens: int,
    model: str | None = None,
    reasoning_tokens: int = 0,
) -> dict:
    """Full canonical cost record for an LLM_CALL (CONTRATO §3.1)."""
    pin, rate_in, ver_in = _pricing_meta(model, "input")
    pout, rate_out, ver_out = _pricing_meta(model, "output")
    prsn, rate_rsn, ver_rsn = _pricing_meta(model, "reasoning")

    pin = settings.price_input_usd_per_1m if pin is None else pin
    pout = settings.price_output_usd_per_1m if pout is None else pout
    prsn = 0.0 if prsn is None else prsn

    rate = rate_in or rate_out or rate_rsn or settings.usd_brl_rate
    version = ver_in or ver_out or ver_rsn or PLACEHOLDER_VERSION

    cost_usd = (
        (input_tokens / 1_000_000) * pin
        + (output_tokens / 1_000_000) * pout
        + (reasoning_tokens / 1_000_000) * prsn
    )
    return {
        "cost_usd": cost_usd,
        "cost_brl": cost_usd * rate,
        "usd_brl_rate": rate,
        "pricing_version": version,
    }


def audio_cost_breakdown(seconds: float, model: str = "whisper-1") -> dict:
    """Full canonical cost record for a WHISPER_TRANSCRIPTION (CONTRATO §3.2)."""
    price, rate, version = _pricing_meta(model, "audio_minute")
    price = settings.whisper_price_usd_per_min if price is None else price
    rate = settings.usd_brl_rate if rate is None else rate
    version = PLACEHOLDER_VERSION if version is None else version
    cost_usd = math.ceil(seconds / 60.0) * price
    return {
        "cost_usd": cost_usd,
        "cost_brl": cost_usd * rate,
        "usd_brl_rate": rate,
        "pricing_version": version,
    }
