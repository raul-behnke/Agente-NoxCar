"""Seed/refresh the `pricing` table with CONFIRMED OpenAI prices (GAP-2).

Replaces the placeholder env defaults (cost.py falls back to env only when no
pricing row matches). Run after confirming current prices with OpenAI billing:

    python -m scripts.seed_pricing

v2 / CONTRATO_EVENTOS_CANONICO §4: each row carries usd_brl_rate + pricing_version
so every LLM_CALL/WHISPER event the Hub ingests is reconcilable. Bump
PRICING_VERSION when you change prices/rate; each run inserts a new effective_from
row (history kept; cost.py reads the latest).

IMPORTANT: values below mirror the current env PLACEHOLDERS — behavior does not
change until a human confirms real billing numbers and edits this file.
"""
from __future__ import annotations

from db.engine import connect, init_db

PRICING_VERSION = "2026-06-17"
USD_BRL_RATE = 5.40

# (model, kind, price_usd)  kind: 'input'|'output'|'reasoning' per 1M tokens;
#                                 'audio_minute' per minute
PRICES = [
    ("gpt-5-mini", "input", 0.25),    # TODO confirm with OpenAI billing
    ("gpt-5-mini", "output", 2.00),   # TODO confirm with OpenAI billing
    ("gpt-5-mini", "reasoning", 2.00),  # TODO confirm (reasoning often = output)
    ("whisper-1", "audio_minute", 0.006),  # TODO confirm with OpenAI billing
]


def seed() -> None:
    init_db()
    with connect() as con:
        for model, kind, price in PRICES:
            con.execute(
                "INSERT INTO pricing(model, kind, price_usd, usd_brl_rate, pricing_version) "
                "VALUES (?, ?, ?, ?, ?)",
                (model, kind, price, USD_BRL_RATE, PRICING_VERSION),
            )
    print(f"seeded {len(PRICES)} pricing rows (version {PRICING_VERSION})")


if __name__ == "__main__":
    seed()
