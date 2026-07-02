"""Central configuration. Reads from environment; never hardcode secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # load adrian/.env if python-dotenv is available (dev convenience)
    from dotenv import load_dotenv
    from pathlib import Path

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # noqa: BLE001 - dotenv optional
    pass


@dataclass(frozen=True)
class Settings:
    # Model (V1 per PRD §12.2)
    model_id: str = os.getenv("ADRIAN_MODEL_ID", "gpt-5-mini")
    # Reasoning effort for gpt-5/o-series (minimal|low|medium|high). Default
    # "medium" na OpenAI é LENTO (~3 chamadas sequenciais/turno). "low" corta
    # bastante a latência sem perder qualidade em extração/atendimento.
    reasoning_effort: str = os.getenv("ADRIAN_REASONING_EFFORT", "low")

    # Persistence: CRM history is the source of truth; this DB is operational
    # support only (audit, dedup, attempt counters, escalation/booking flags).
    db_file: str = os.getenv("ADRIAN_DB_FILE", "adrian.db")

    # Activation tag (PRD §4.1). Adrian only acts when this tag is present.
    activation_tag: str = os.getenv("ADRIAN_ACTIVATION_TAG", "agente-ia")

    # Max times Adrian may re-ask for the same missing field (PRD §4.8).
    max_insist_attempts: int = int(os.getenv("ADRIAN_MAX_INSIST", "2"))

    # History turns replayed into context.
    num_history_runs: int = int(os.getenv("ADRIAN_HISTORY_RUNS", "8"))

    # Timeouts (s) para evitar turno travado em silêncio. Um hang no CRM ou na
    # chamada LLM vira erro visível -> _safe_escalate, em vez de sumir.
    crm_timeout_sec: float = float(os.getenv("ADRIAN_CRM_TIMEOUT_SEC", "20"))
    llm_timeout_sec: float = float(os.getenv("ADRIAN_LLM_TIMEOUT_SEC", "60"))
    turn_timeout_sec: float = float(os.getenv("ADRIAN_TURN_TIMEOUT_SEC", "150"))

    # Burst debounce (WhatsApp): quando o lead manda várias mensagens seguidas,
    # espera esta janela (s) e processa só a ÚLTIMA — evita turnos preemptados e
    # perda da 1ª mensagem (ex.: o nome). 0 desliga. A history do CRM já terá o
    # burst completo quando o turno vencedor rodar (aggregate_burst).
    burst_debounce_sec: float = float(os.getenv("ADRIAN_BURST_DEBOUNCE_SEC", "6"))

    # CRM (GoHighLevel-style) integration.
    crm_base_url: str = os.getenv("CRM_BASE_URL", "https://services.leadconnectorhq.com")
    crm_api_key: str = os.getenv("CRM_API_KEY", "")
    crm_location_id: str = os.getenv("CRM_LOCATION_ID", "")

    # Stock JSON is stored in a CRM Custom Value, synced every 3h (PRD §7.1.1).
    stock_custom_value_id: str = os.getenv("CRM_STOCK_CUSTOM_VALUE_ID", "")

    # Human-routing workflow the contact is added to on escalation (PRD §6.5).
    escalation_workflow_id: str = os.getenv("CRM_ESCALATION_WORKFLOW_ID", "")

    # Calendar used for bookings (PRD §8.5).
    calendar_id: str = os.getenv("CRM_CALENDAR_ID", "")
    app_timezone: str = os.getenv("ADRIAN_TZ", "America/Sao_Paulo")
    appointment_duration_min: int = int(os.getenv("ADRIAN_APPT_MIN", "60"))

    # Vídeo da estrutura enviado 1x na saudação (URL, type SMS). "" desliga.
    greeting_video_url: str = os.getenv(
        "URL_VIDEO",
        "https://assets.cdn.filesafe.space/wbSVxrr4mvYNRaIw1eJ7/media/6a452aaab653a0ddc229a739.mp4",
    )

    # Shared secret the CRM appends to the inbound webhook (see security.py).
    webhook_secret: str = os.getenv("ADRIAN_WEBHOOK_SECRET", "")
    export_secret: str = os.getenv("ADRIAN_EXPORT_SECRET", "")

    # FAQ Custom Value id (YAML) in the CRM (PRD §7.2 / reference parity).
    faq_custom_value_id: str = os.getenv("CRM_FAQ_CUSTOM_VALUE_ID", "")

    # Canonical event envelope (v2 / CONTRATO_EVENTOS_CANONICO §2).
    client_slug: str = os.getenv("ZOI_CLIENT_SLUG", "noxcar")
    agent_slug: str = os.getenv("ZOI_AGENT_SLUG", "adrian-noxcar")

    # Opportunities/pipeline (Fase 3 / GAP-5). Commercial sync is skipped unless
    # both are set, so the agent runs unchanged where the pipeline isn't wired.
    crm_pipeline_id: str = os.getenv("CRM_PIPELINE_ID", "")
    crm_pipeline_stage_id: str = os.getenv("CRM_PIPELINE_STAGE_ID", "")

    # Follow-up / abandonment sweeps (Fase 2/3). Minutes of inactivity before a
    # non-terminal session gets a re-engagement nudge, then is marked abandoned.
    followup_idle_min: int = int(os.getenv("ADRIAN_FOLLOWUP_IDLE_MIN", "1440"))   # 24h
    abandon_idle_min: int = int(os.getenv("ADRIAN_ABANDON_IDLE_MIN", "4320"))     # 72h
    max_followups: int = int(os.getenv("ADRIAN_MAX_FOLLOWUPS", "1"))

    # --- Token cost tracking (PLACEHOLDER prices — confirm with OpenAI billing) ---
    # USD per 1M tokens for the chat model (gpt-5-mini). Override via env.
    price_input_usd_per_1m: float = float(os.getenv("OPENAI_PRICE_IN_PER_1M", "0.25"))
    price_output_usd_per_1m: float = float(os.getenv("OPENAI_PRICE_OUT_PER_1M", "2.00"))
    # USD -> BRL conversion rate for cost reporting in R$.
    usd_brl_rate: float = float(os.getenv("USD_BRL_RATE", "5.40"))
    # Whisper transcription price (USD per audio minute) — PLACEHOLDER, confirm billing.
    whisper_price_usd_per_min: float = float(os.getenv("OPENAI_WHISPER_PRICE_PER_MIN", "0.006"))

    # Photo caps (avoid spamming WhatsApp; vehicles can have 14+ photos each).
    photos_per_vehicle_single: int = int(os.getenv("PHOTOS_PER_VEHICLE_SINGLE", "4"))
    photos_per_vehicle_list: int = int(os.getenv("PHOTOS_PER_VEHICLE_LIST", "1"))
    photos_total_max: int = int(os.getenv("PHOTOS_TOTAL_MAX", "6"))


settings = Settings()
