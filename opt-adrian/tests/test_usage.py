"""Sprint 8 — token/cost tracking. Pure + DB, no LLM/network."""
from __future__ import annotations

import os
import tempfile

os.environ["ADRIAN_DB_FILE"] = os.path.join(tempfile.gettempdir(), "adrian_usage_test.db")

import pytest  # noqa: E402

from cost import compute_cost_brl, compute_cost_usd  # noqa: E402
from db.engine import init_db  # noqa: E402
from db.usage import usage_totals  # noqa: E402
from usage import record_usage  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    if os.path.exists(os.environ["ADRIAN_DB_FILE"]):
        os.remove(os.environ["ADRIAN_DB_FILE"])
    init_db()


def test_compute_cost_usd_explicit_prices():
    # 1M prompt @ $0.25 + 1M completion @ $2.00 = $2.25
    usd = compute_cost_usd(1_000_000, 1_000_000, price_in_per_1m=0.25, price_out_per_1m=2.0)
    assert round(usd, 4) == 2.25


def test_compute_cost_brl_applies_rate():
    brl = compute_cost_brl(
        1_000_000, 0, rate=5.0, price_in_per_1m=1.0, price_out_per_1m=0.0
    )
    assert round(brl, 4) == 5.0  # $1.00 * 5.0


def test_compute_cost_zero():
    assert compute_cost_brl(0, 0) == 0.0


def test_record_usage_persists_and_totals():
    record_usage("updater", 1000, 500, model="gpt-5-mini")
    record_usage("voice", 2000, 800, model="gpt-5-mini")
    totals = usage_totals()
    assert totals["prompt_tokens"] == 3000
    assert totals["completion_tokens"] == 1300
    assert totals["cost_brl"] > 0


def test_record_usage_returns_cost():
    cost = record_usage("inventory_expert", 1_000_000, 0)
    # default price_in 0.25 USD * 5.40 rate = 1.35 BRL
    assert round(cost, 4) == 1.35
