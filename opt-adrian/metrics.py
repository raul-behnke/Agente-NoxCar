"""Prometheus metrics (reference parity: metrics.py).

Exposes turn/handoff/qualification counters, LLM latency, and — per request —
token usage and BRL cost per component. Import-safe even if prometheus_client is
missing (no-op stubs) so tests/deterministic paths never break.
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram, generate_latest  # noqa: F401

    _ENABLED = True
except ImportError:  # pragma: no cover
    _ENABLED = False

    class _Noop:
        def labels(self, *a, **k):
            return self

        def inc(self, *a, **k):
            return None

        def observe(self, *a, **k):
            return None

    def Counter(*a, **k):  # type: ignore
        return _Noop()

    def Histogram(*a, **k):  # type: ignore
        return _Noop()

    def generate_latest(*a, **k):  # type: ignore
        return b""


TURNS_TOTAL = Counter("adrian_turns_total", "Turns by final action", ["action"])
HANDOFF_TOTAL = Counter("adrian_handoff_total", "Handoffs by terminal reason", ["reason"])
QUALIFICADOS_TOTAL = Counter("adrian_qualificados_total", "Qualified outcomes", ["outcome"])
LLM_LATENCY = Histogram("adrian_llm_latency_seconds", "LLM call latency", ["component"])

# Token + cost tracking (per user request)
TOKENS_TOTAL = Counter("adrian_tokens_total", "Tokens used", ["component", "kind"])
COST_BRL_TOTAL = Counter("adrian_cost_brl_total", "Estimated cost in BRL", ["component"])


def render_metrics() -> bytes:
    return generate_latest()
