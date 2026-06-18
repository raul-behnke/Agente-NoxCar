# RELATÓRIO DE ADEQUAÇÃO APLICADA — AGENTE ADRIAN (NOX CAR)
> Implementação do `PLANO_ADEQUACAO_TELEMETRIA_NOXCAR.md` para o **ZOI Performance Hub**
> Alvo: `opt-adrian/` (clone local de `/opt/adrian`). VPS de produção **ainda não atualizada** (deploy pendente de aprovação).
> Data: 2026-06-17

---

## RESUMO

As 3 fases do plano foram implementadas e validadas. **Suíte: 110 passed, 6 skipped (evals LLM opt-in), zero regressão.** Cada gap recebeu validação E2E dedicada (custo por lead, pricing override, Whisper, eventos, sweeps, opportunity, export).

| Fase | Foco | Status |
|---|---|---|
| **1** | FK custo↔conversa, total_tokens, Whisper, pricing confirmado | ✅ Aplicada |
| **2** | Tabela `events` append-only, abandono/timeout, export SQLite | ✅ Aplicada |
| **3** | Opportunities GHL, follow-up/re-engajamento | ✅ Aplicada |
| **v2** | Envelope canônico, secret obrigatório no export, reconciliação de custo | ✅ Aplicada |

> **v2:** rodada de correção contra `CONTRATO_EVENTOS_CANONICO.md` (v1, schema_version 1). Suíte: **115 passed, 6 skipped** (110 mantidos + 5 novos em `tests/test_v2_canonical.py`).

Princípio mantido: **toda telemetria é best-effort** — nenhuma falha de contabilidade/CRM/evento quebra um turno. Recursos comerciais ficam inativos até serem configurados (sem mudar comportamento onde o pipeline não está ligado).

---

## FASE 1 — FINANCEIRO (GAP-1 a GAP-4)

### GAP-1 — FK custo↔conversa *(intervenção #1)*
- `db/engine.py`: `_migrate()` idempotente adiciona `contact_id`, `conversation_id`, `total_tokens` em `token_usage` + índice `idx_token_usage_contact`. PRAGMA-guard, roda em todo `init_db()` (seguro em prod com dados existentes — linhas históricas ficam com `contact_id=NULL`).
- `obs.py`: helpers `bind_ids()` / `clear_ids()` via `structlog.contextvars` (fallback no-op sem structlog).
- `orchestrator.py`: bind em `process_turn` (cobre os 3 LLMs do turno); `endpoints/inbound.py`: bind em `_process_inbound` (cobre Whisper, que roda antes do turno).
- `usage.py`: `record_usage` lê ids do contexto (`_ctx_ids`) — **sem alterar assinaturas** de `llm.parse_structured` nem `team/usage.py`.
- `db/usage.py`: `record_usage_row` grava FK; **novo** `usage_by_contact(contact_id)` → custo/tokens por lead.
- `app.py`: **novo** endpoint `GET /usage/{contact_id}`.

### GAP-3 — total_tokens
- Calculado (`prompt+completion`) e gravado em `record_usage`; exposto em `usage_totals()`.

### GAP-2 — preços confirmados
- `db/engine.py`: tabela `pricing(model, kind, price_usd, effective_from)`.
- `cost.py`: resolução **explícito → tabela `pricing` → env (placeholder)**; `compute_cost_usd`/`compute_cost_brl` ganham `model`. Comportamento inalterado até seed (tabela vazia → fallback env).
- `scripts/seed_pricing.py`: seed versionado (mantém valores atuais marcados `TODO confirm billing`; cada run cria nova linha `effective_from`, histórico preservado).

### GAP-4 — Whisper contabilizado
- `audio/whisper.py`: `response_format="verbose_json"` → lê `duration`; chama `record_audio_usage("whisper", segundos)`.
- `cost.py`: `compute_audio_cost_brl(segundos, model)` (preço por minuto via `pricing` audio_minute → fallback `settings.whisper_price_usd_per_min`).
- `usage.py`: `record_audio_usage` (custo por minuto, duração em `total_tokens`).
- `config/settings.py`: `whisper_price_usd_per_min` (default 0.006, placeholder).

**Validação F1:** custo por lead (`usage_by_contact` calls=4, cost>0); 1M input default = 1,35 BRL (inalterado); override via `pricing` = 5,40 BRL; Whisper 60s = 0,0648 BRL; chamada sem contexto grava `null` sem quebrar; `init_db()` idempotente (3×).

---

## FASE 2 — OPERACIONAL E DURABILIDADE (GAP-7, GAP-9, GAP-10)

### Tabela `events` append-only (GAP-10)
- `db/engine.py`: `events(id, event_type, contact_id, conversation_id, payload JSON, created_at)` + índices por contato e por tipo.
- **novo** `db/events.py`: `record_event(...)` (best-effort) e `events_since(since_id, limit)` (pull incremental do Hub).
- Emissão nos ganchos reais de `orchestrator.py`:
  - `CONVERSATION_STARTED` — sessão nova (via `session_exists`).
  - `CONVERSATION_COMPLETED` — sempre que `terminal_reason` é setado (escalação + booking).
  - `HANDOFF_CREATED` — em `_escalate`.
  - `APPOINTMENT_CREATED` — no booking.
  - `WHISPER_TRANSCRIPTION` — em `audio/whisper.py`.
- Nota: `LLM_CALL` continua servido pela tabela `token_usage` (feed canônico de custo por chamada).

### Abandono / timeout (GAP-7)
- `db/sessions.py`: **novos** `session_exists()` e `stale_active_sessions(idle_minutes)`.
- **novo** `scripts/sweep_abandoned.py`: marca sessões não-terminais ociosas > `abandon_idle_min` como `terminal_reason=abandonado` + emite `CONVERSATION_ABANDONED`. (`abandonado` existia no enum mas **nunca era setado** — agora tem fonte de timeout.) Rodar via cron/systemd timer.

### Export / replicação SQLite (GAP-9)
- `db/usage.py`: `usage_since(since_id, limit)`.
- **novo** `endpoints/export.py`: `GET /export/usage?since=` e `GET /export/events?since=`, **autenticados** por `require_secret` (reforça GAP-11), limite 5000. Coletor do Hub puxa por `max_id`.
- `app.py`: router incluído.

**Validação F2:** eventos emitidos na ordem correta no booking; sweep de abandono marcou sessão de 100 dias; `events_since`/`usage_since` retornam incremental.

---

## FASE 3 — COMERCIAL E RE-ENGAJAMENTO (GAP-5, GAP-7)

### Opportunities GHL (GAP-5)
- **novo** `ghl/opportunities.py` (`OpportunitiesMixin`): `create_opportunity` (POST `/opportunities/`) e `update_opportunity` (PUT `/opportunities/{id}`) no pipeline configurado.
- `ghl/client.py`: mixin adicionado ao `GhlClient`.
- `agent/schemas.py`: `SessionState.opportunity_id` (persiste com o snapshot, evita recriar).
- `orchestrator.py`: `_sync_opportunity` chamado ao qualificar (`qualificado_agendado` no booking, `qualificado_sem_agenda` na escalação) → cria/atualiza opportunity + emite `OPPORTUNITY_CREATED`. **Gated em config** (`crm_pipeline_id` + `crm_pipeline_stage_id`); best-effort.
- `config/settings.py`: `crm_pipeline_id`, `crm_pipeline_stage_id`.

### Follow-up / re-engajamento (GAP-7)
- `agent/schemas.py`: `followup_count`, `followup_pending`.
- **novo** `scripts/sweep_followups.py`: envia 1 nudge a sessões ociosas na janela `followup_idle_min`..`abandon_idle_min`, marca `followup_pending`, emite `FOLLOWUP_STARTED`.
- `orchestrator.py`: ao retornar o lead (próximo inbound), emite `FOLLOWUP_FINISHED` e limpa `followup_pending`.
- `config/settings.py`: `followup_idle_min` (24h), `abandon_idle_min` (72h), `max_followups` (1).

**Validação F3:** booking criou opportunity (`opp123` armazenado em `opportunity_id`) + `OPPORTUNITY_CREATED`; sweep follow-up enviou nudge e setou `followup_pending`; inbound seguinte emitiu `FOLLOWUP_FINISHED` e limpou flag.

---

## CORREÇÕES v2 — ALINHAMENTO AO CONTRATO CANÔNICO

Validação central achou só gaps de **envelope** + **segurança** vs `CONTRATO_EVENTOS_CANONICO.md`. O que já estava certo (FK custo↔conversa, custo BRL, Whisper verbose_json, `events`, abandono, Opportunities, follow-up, transporte `/export/*`) foi mantido. Correções cirúrgicas:

### 1. Envelope canônico nos eventos (CONTRATO §2)
- `db/engine.py`: migração idempotente PRAGMA-guard generalizada (`_add_missing`) adiciona à tabela `events` → `event_id`, `schema_version`, `client`, `agent`, `occurred_at` + **índice UNIQUE em `event_id`** (chave de idempotência; o Hub deduplica por ela). Pré-v2 rows ficam com `event_id=NULL` (SQLite permite múltiplos NULL em UNIQUE).
- `db/events.py`: `record_event` gera `event_id=uuid4()`, `schema_version=1`, `client="noxcar"`, `agent="adrian-noxcar"`, `occurred_at` (ISO8601 UTC) e retorna o `event_id`. `events_since` serializa o envelope completo + `cursor_id`.
- `config/settings.py`: `client_slug` / `agent_slug` (env `ZOI_CLIENT_SLUG` / `ZOI_AGENT_SLUG`).

### 2. Reconciliação de custo no LLM_CALL/WHISPER (CONTRATO §3.1/§3.2/§4)
- `db/engine.py`: `token_usage` ganha `event_id`, `cost_usd`, `usd_brl_rate`, `pricing_version`, `reasoning_tokens`; tabela `pricing` ganha `usd_brl_rate`, `pricing_version` (kind agora inclui `reasoning`).
- `cost.py`: `cost_breakdown()` / `audio_cost_breakdown()` retornam `cost_usd`, `cost_brl`, `usd_brl_rate`, `pricing_version`. Whisper agora arredonda minutos **pra cima** (`ceil`, §4). Funções antigas mantidas (back-compat dos testes).
- `usage.py`: `record_usage`/`record_audio_usage` geram `event_id`, gravam o breakdown completo, aceitam `reasoning_tokens`.
- `llm.py`: extrai `reasoning_tokens` de `completion_tokens_details` (família gpt-5).
- `db/usage.py`: `usage_since` serializa cada linha como evento canônico `LLM_CALL` (ou `WHISPER_TRANSCRIPTION`) com envelope + payload de custo. `get_pricing()` retorna preço+rate+versão.
- `scripts/seed_pricing.py`: grava `usd_brl_rate` + `pricing_version` (`2026-06-17`) + kind `reasoning`.

### 3. Secret obrigatório no export (GAP-11, CONTRATO §5)
- `security.py`: **novo** `require_secret_strict` — recusa quando o secret está **ausente OU não configurado** (diferente do `require_secret` do webhook, que liberava em dev).
- `endpoints/export.py`: `/export/usage` e `/export/events` retornam **HTTP 401** real (JSONResponse) sem secret válido. Export nunca fica aberto.

### Validação v2 (E2E + suíte)
- Evento com `event_id` único + `client`/`agent`/`schema_version`/`occurred_at`. ✓
- `/export/events` e `/export/usage` recusam 401 sem secret (secret unset → sempre 401; secret set → 401 sem/errado, 200 correto). ✓
- Payload de custo traz `cost_brl` + `cost_usd` + `usd_brl_rate` + `pricing_version` + tokens crus (`input/output/total/reasoning`) para reconciliação `hub_cost_brl`. ✓
- Whisper 90s → 2 min (ceil) = 0,0648 BRL. ✓
- `pricing_version` flui do placeholder (`placeholder-env`) para a versão confirmada (`2026-06-17`) após seed. ✓

---

## ARQUIVOS

**Novos (7):** `db/events.py`, `endpoints/export.py`, `ghl/opportunities.py`, `scripts/seed_pricing.py`, `scripts/sweep_abandoned.py`, `scripts/sweep_followups.py`, `tests/test_v2_canonical.py`.

**Alterados (15):** `db/engine.py`, `db/usage.py`, `db/sessions.py`, `db/events.py`, `usage.py`, `cost.py`, `llm.py`, `security.py`, `obs.py`, `orchestrator.py`, `audio/whisper.py`, `agent/schemas.py`, `config/settings.py`, `ghl/client.py`, `endpoints/inbound.py`, `endpoints/export.py`, `app.py`, `.env.example`.

---

## EVENTOS EMITIDOS (mapa final)

| Evento | Origem | Fase |
|---|---|---|
| `CONVERSATION_STARTED` | `orchestrator` sessão nova | 2 |
| `CONVERSATION_COMPLETED` | `orchestrator` terminal set | 2 |
| `HANDOFF_CREATED` | `_escalate` | 2 |
| `APPOINTMENT_CREATED` | booking | 2 |
| `CONVERSATION_ABANDONED` | `sweep_abandoned` | 2 |
| `WHISPER_TRANSCRIPTION` | `audio/whisper` | 2 |
| `OPPORTUNITY_CREATED` | `_sync_opportunity` | 3 |
| `FOLLOWUP_STARTED` | `sweep_followups` | 3 |
| `FOLLOWUP_FINISHED` | `orchestrator` retorno do lead | 3 |
| `LLM_CALL` *(via `token_usage`)* | `usage.record_usage` | 1 |

---

## COMO O HUB CONSOME

1. **Custo por lead/componente:** `GET /export/usage?since=<id>&secret=` (token_usage com FK) ou `GET /usage/{contact_id}`.
2. **Eventos de negócio:** `GET /export/events?since=<id>&secret=`.
3. **Totais rápidos:** `GET /usage`.
4. **Série temporal:** scrape `GET /metrics` (Prometheus do Hub com retenção — resolve contadores voláteis, GAP-8).

---

## PENDÊNCIAS / PRÉ-REQUISITOS OPERACIONAIS

| Item | Ação |
|---|---|
| **Deploy VPS** | rsync `opt-adrian/` → `/opt/adrian` + `systemctl restart adrian`. Migração roda automática no startup. **Não executado** (aguarda aprovação). |
| Preços reais | Editar `scripts/seed_pricing.py` com billing OpenAI confirmado e rodar `python -m scripts.seed_pricing`. |
| Pipeline comercial | Setar `CRM_PIPELINE_ID` + `CRM_PIPELINE_STAGE_ID` no `.env` (senão opportunity é pulada). |
| Sweeps | Agendar cron/systemd timer: `scripts.sweep_followups` e `scripts.sweep_abandoned` (horário). |
| Webhook secret | **Obrigatório** setar `ADRIAN_WEBHOOK_SECRET` — sem ele os endpoints `/export/*` retornam 401 (v2). Webhook inbound segue liberando em dev. |

---

## SCORE PROJETADO

Baseline ~5,4/10 → **~8,3/10** após deploy + configuração dos pré-requisitos acima. Maior salto/menor custo já entregue: FK custo↔conversa (Fase 1).
