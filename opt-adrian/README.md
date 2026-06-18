# Adrian — Nox Car Pre-Attendance Agent (Agno)

Commercial pre-attendance SDR for inbound dealership leads (WhatsApp via GoHighLevel).
Built on Agno following the organization's reference architecture: a deterministic
orchestrator wrapping a **3-stage intelligence pipeline**.

## Architecture (3 stages per turn)

```
Lead → CRM → POST /webhook/inbound → orchestrator.process_turn (contactId preemption)
  1. activation gate (tag "agente-ia")            [code]
  2. dedup + burst aggregation (GHL debounces)     [code]
  3. load SessionState + terminal/human guards     [code]
  4. Updater  — extract StateUpdate                [LLM #1, gpt-5-mini, temp 0.0]
  5. merge_into_state                              [code]
  6. Question Planner — next funnel question       [code, no LLM]
  7. EstoqueExpert — which vehicles to show        [LLM #2]  ── via run_team_turn
  8. Adrian voice — BubbleSequence                 [LLM #3]  ─┘
  9. send photos + bubbles; book / escalate        [code]
```

Hard business rules live in code (orchestrator/planner), never in prompts. Grill
decisions are recorded in `../DECISOES_GRILL_ADRIAN.md`.

## Layout

```
adrian/
├── app.py                  # FastAPI (/webhook/inbound, /sessions/{id}/greet, /health, /metrics, /usage)
├── orchestrator.py         # 16-step per-turn pipeline + contactId preemption
├── config/settings.py      # env-driven config (model, CRM ids, pricing)
├── obs.py, metrics.py, cache.py, security.py, cost.py, usage.py
├── agent/                  # NON-Agno: schemas, updater, question_planner, merge, templates
├── team/                   # Agno: inventory_expert, voice, runner, validation, rendering, schemas, usage
├── tools/                  # faq, inventory, photos, handoff, terminal
├── ghl/                    # modular GoHighLevel client
├── audio/whisper.py        # async transcription
├── db/                     # SQLite: sessions, dedup/idempotency, token_usage
├── endpoints/              # inbound (burst+audio), greet, ingest helpers
└── tests/                  # 93 deterministic tests (+6 opt-in LLM evals)
```

## Setup

```bash
cd adrian
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill OPENAI_API_KEY + CRM_* (+ confirm pricing knobs)
```

## Run

```bash
uvicorn app:api --reload      # webhook + /metrics + /usage + /health
python app.py                 # AgentOS UI + tracing (local dev)
```

## Test

```bash
pytest                        # 93 deterministic tests (no network/quota)
RUN_LLM_EVALS=1 pytest tests/test_updater.py   # opt-in real extraction (needs FUNDED key)
```

## Observability & cost

- `GET /metrics` — Prometheus: turns, handoffs, qualifications, LLM latency,
  **tokens** (`adrian_tokens_total{component,kind}`) and **BRL cost**
  (`adrian_cost_brl_total{component}`).
- `GET /usage` — aggregate token + R$ totals from the `token_usage` audit table.
- Every LLM call (Updater / EstoqueExpert / voice) records tokens + cost via
  `usage.record_usage`. Prices are configurable placeholders — confirm against
  OpenAI billing in `.env` (`OPENAI_PRICE_*`, `USD_BRL_RATE`).

## Production notes
- CRM history is canonical; SQLite is operational support (sessions, dedup, usage).
- Swap SQLite for Postgres JSONB (next cycle); inventory whole-in-prompt holds to ~60 vehicles.
- Every side effect (booking, escalation) is idempotent — safe under webhook retries.
- Integration failure → escalate to human with a note (PRD §12.8).
- Adapt the `/webhook/inbound` payload mapping (`endpoints/ingest.extract_payload`)
  and `ghl/` endpoints to your CRM's real schema.
```
