"""OpenAI wrapper (reference parity: llm.py).

`parse_structured` forces a Pydantic-typed structured output from the model,
used by the Updater (and reusable elsewhere). Direct OpenAI SDK — NOT Agno — for
the deterministic extraction step (mirrors the reference's non-Agno Updater).

gpt-5 models may reject `temperature != 1`; we attempt the requested temperature
and transparently retry at the model default if the API refuses it.
"""
from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from config.settings import settings
from obs import log

T = TypeVar("T", bound=BaseModel)

_client = None


def _omit_temperature(model: str) -> bool:
    """Reasoning models (gpt-5 family, o1/o3/o4) reject temperature != 1. Skip
    sending it entirely to avoid a guaranteed failed call + retry every turn
    (each retry was a second slow reasoning request blocking the pipeline)."""
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI()  # reads OPENAI_API_KEY from env
    return _client


def parse_structured(
    messages: list[dict],
    schema: Type[T],
    model: str | None = None,
    temperature: float | None = 0.0,
    component: str = "llm",
    reasoning_effort: str | None = None,
    max_completion_tokens: int | None = None,
) -> T:
    """Call the model and return a validated instance of `schema`.

    Records token usage + BRL cost under `component`.
    """
    import time

    from metrics import LLM_LATENCY
    from usage import record_usage

    client = _get_client()
    model = model or settings.model_id
    kwargs: dict = {"model": model, "messages": messages, "response_format": schema}
    # gpt-5/o-series reject temperature != 1 — don't send it (avoids a guaranteed
    # failed call + slow reasoning retry every turn). Retry below stays as safety.
    if temperature is not None and not _omit_temperature(model):
        kwargs["temperature"] = temperature
    # reasoning models: cap reasoning effort to keep the turn fast (default medium
    # is slow). Only valid for gpt-5/o-series.
    if _omit_temperature(model):
        kwargs["reasoning_effort"] = reasoning_effort or settings.reasoning_effort
        # backstop contra runaway de raciocínio (visto 12k+ tokens estourando o
        # timeout e escalando por engano). Limita reasoning+saída.
        cap = max_completion_tokens or settings.max_completion_tokens
        if cap:
            kwargs["max_completion_tokens"] = cap

    started = time.perf_counter()
    try:
        completion = client.beta.chat.completions.parse(**kwargs)
    except Exception as exc:  # noqa: BLE001
        # Some gpt-5 models only allow the default temperature -> retry without it.
        if temperature is not None and "temperature" in str(exc).lower():
            log.warning("temperature_unsupported", model=model, note="retrying at default")
            kwargs.pop("temperature", None)
            completion = client.beta.chat.completions.parse(**kwargs)
        else:
            raise
    LLM_LATENCY.labels(component).observe(time.perf_counter() - started)

    usage = getattr(completion, "usage", None)
    if usage is not None:
        # reasoning tokens (gpt-5 family) live under completion_tokens_details
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", 0) if details else 0
        record_usage(
            component,
            getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
            model=model,
            reasoning_tokens=reasoning or 0,
        )
    return completion.choices[0].message.parsed
