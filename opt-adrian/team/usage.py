"""Extract token usage from an Agno RunOutput and record it.

Agno exposes per-run token metrics; field names vary across versions, so we probe
several and degrade to zero (still logs the call) rather than break the turn.
"""
from __future__ import annotations

from obs import log
from usage import record_usage


def _agno_tokens(result) -> tuple[int, int]:
    metrics = getattr(result, "metrics", None)
    if metrics is None:
        return 0, 0
    # try common shapes: attributes or dict
    def _get(obj, *names):
        for n in names:
            if isinstance(obj, dict) and obj.get(n) is not None:
                return obj[n]
            if getattr(obj, n, None) is not None:
                return getattr(obj, n)
        return 0

    def _scalar(v):
        # some Agno metrics are lists per message; sum them
        if isinstance(v, (list, tuple)):
            return sum(int(x or 0) for x in v)
        return int(v or 0)

    prompt = _scalar(_get(metrics, "input_tokens", "prompt_tokens"))
    completion = _scalar(_get(metrics, "output_tokens", "completion_tokens"))
    return prompt, completion


def record_agno_usage(component: str, result) -> float:
    try:
        prompt, completion = _agno_tokens(result)
        return record_usage(component, prompt, completion)
    except Exception as exc:  # noqa: BLE001
        log.warning("agno_usage_failed", component=component, error=str(exc))
        return 0.0
