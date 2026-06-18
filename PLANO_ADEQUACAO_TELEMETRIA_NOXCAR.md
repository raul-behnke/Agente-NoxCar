# PLANO DE ADEQUAÇÃO DE TELEMETRIA — AGENTE ADRIAN (NOX CAR)
> Adequação ao **ZOI Performance Hub**
> Agente: Adrian — Nox Car · VPS 147.79.87.179 (`adrian-nox.appzoi.com.br`, `adrian.service`)
> Stack: Python/FastAPI · Gunicorn+Uvicorn (2w) :8123 · Nginx TLS · SQLite · OpenAI `gpt-5-mini`

---

## 1. SITUAÇÃO ATUAL

**Adrian é o agente financeiramente mais maduro da frota.** A base de custo já existe e funciona — o problema é **atribuição e precisão**, não ausência.

O que já está pronto (não reconstruir):
- **Tabela `token_usage`** (`db/engine.py`): `id, component, model, prompt_tokens, completion_tokens, cost_brl, created_at`. Populada a cada chamada LLM via `usage.record_usage()` → `db/usage.py:record_usage_row()`.
- **Cálculo de custo em BRL** (`cost.py`): `cost_usd = (prompt/1e6)*price_in + (completion/1e6)*price_out`; `cost_brl = cost_usd * usd_brl_rate`. Configurável via env `OPENAI_PRICE_IN_PER_1M` / `OPENAI_PRICE_OUT_PER_1M` / `USD_BRL_RATE`.
- **3 componentes instrumentados**: `updater` (via `llm.parse_structured`), `inventory_expert` e `voice` (via `team/usage.py:record_agno_usage`). Dados vivos confirmam: 24 chamadas, 3 componentes, todos `gpt-5-mini`.
- **Endpoints de telemetria**: `/usage` (totais BRL+tokens via `db/usage.py:usage_totals`), `/metrics` (Prometheus), `/health`.
- **Métricas Prometheus** (`metrics.py`): `adrian_turns_total`, `adrian_handoff_total`, `adrian_qualificados_total`, `adrian_llm_latency_seconds`, `adrian_tokens_total`, `adrian_cost_brl_total`.
- **Idempotência de desfecho**: `side_effects` (`kind ∈ {escalation, booking}`) e `sessions.terminal_reason` — base pronta para eventos de negócio.

**Baseline de aderência: ~5,4/10 — o mais alto da frota.**

---

## 2. GAPS

### Financeiro (foco #1 — mais importante e mais barato)
- **GAP-1 (CIRÚRGICO):** `token_usage` **não tem `contact_id` nem `conversation_id`** → custo só existe como total e por componente; **não atribuível por conversa/lead**.
- **GAP-2:** Preços são **PLACEHOLDER** (`cost.py` comenta "confirmar com billing OpenAI"; defaults 0.25 / 2.00 / 5.40) → `cost_brl` impreciso.
- **GAP-3:** `total_tokens` não armazenado (derivável de `prompt+completion`).
- **GAP-4:** **Whisper não contabilizado** em `token_usage` (transcrição de áudio em `audio/whisper.py` roda fora do registro de custo).

### Operacional
- **GAP-7:** Follow-up inexistente; `abandonado` está no enum `TerminalReason` (`tools/terminal.py`) mas **nunca é setado**; sem timeout.
- **GAP-8:** Métricas Prometheus são contadores **in-memory** → resetam no restart do `adrian.service`.
- **GAP-10:** `sessions` é **snapshot sobrescrito** (`save()` faz `ON CONFLICT DO UPDATE`) → sem histórico de transições de estado/funil.
- **GAP-6:** Mensagens não persistidas localmente (só no GHL).
- **GAP-9:** SQLite local sem replicação.
- **GAP-11:** Webhook secret pode estar desabilitado (`security.py:require_secret` retorna True se secret não configurado).

### Comercial
- **GAP-5:** **SEM Opportunities/Pipeline** — `opportunityId` ausente em todo o código (`ghl/` só cobre contacts/conversations/calendars/workflows/custom values) → camada comercial cega; sem atribuição de venda à IA.

---

## 3. ADEQUAÇÕES NECESSÁRIAS

### Banco (`db/engine.py`)
- **`token_usage` + FK:** adicionar colunas `contact_id TEXT`, `conversation_id TEXT`, `total_tokens INTEGER`. Migração idempotente (`ALTER TABLE ... ADD COLUMN` dentro do `SCHEMA`/init guard — SQLite ignora se já existe via checagem `PRAGMA table_info`).
- **Nova tabela `events`** (append-only, fonte de verdade de transições):
  ```sql
  CREATE TABLE IF NOT EXISTS events (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      event_type      TEXT NOT NULL,
      contact_id      TEXT,
      conversation_id TEXT,
      payload         TEXT,            -- JSON livre por tipo
      created_at      TEXT DEFAULT (datetime('now'))
  );
  CREATE INDEX IF NOT EXISTS idx_events_contact ON events(contact_id);
  CREATE INDEX IF NOT EXISTS idx_events_type    ON events(event_type, created_at);
  ```
- **Nova tabela `pricing`** (preços confirmados, substitui placeholder — ver Custos).

### Tokens
- **Propagar `contact_id`/`conversation_id`** até o ponto de registro. Hoje `usage.record_usage(component, prompt_tokens, completion_tokens, model)` não recebe identidade. Cadeia a alterar:
  - `orchestrator.run_turn` já tem `ev.contact_id`/`ev.conversation_id` → injetar em contexto.
  - **Opção recomendada (mínimo toque):** usar `structlog.contextvars` (já há `merge_contextvars` em `obs.py`) — bind `contact_id`/`conversation_id` no início de `run_turn`; `record_usage` lê do contextvar e passa a `record_usage_row`. Evita reescrever assinaturas de `llm.parse_structured` e `team/usage.py`.
- **`total_tokens`:** calcular `prompt+completion` em `record_usage` e gravar na coluna nova.
- **Whisper:** instrumentar `audio/whisper.py` → após transcrição, chamar `record_usage(component="whisper", ...)`. Whisper cobra por **áudio (segundos/minutos)**, não tokens: gravar duração em `prompt_tokens=0`, custo via preço por minuto em `pricing`; registrar evento `WHISPER_TRANSCRIPTION` com duração.

### Custos (`cost.py`)
- **Substituir env placeholder por tabela `pricing`** confirmada com billing OpenAI:
  ```sql
  CREATE TABLE IF NOT EXISTS pricing (
      model            TEXT,
      kind             TEXT,        -- 'input' | 'output' | 'audio_minute'
      price_usd        REAL,        -- por 1M tokens, ou por minuto (audio)
      effective_from   TEXT,
      PRIMARY KEY (model, kind, effective_from)
  );
  ```
  `cost.py:compute_cost_usd` passa a resolver preço por `(model, kind, data)`. **Manter conversão USD→BRL** (`usd_brl_rate`) — idealmente também versionada (cotação na data da chamada).
- Compatibilidade: env vars continuam como fallback se `pricing` vazia.

### Logs
- Manter structlog JSON. Adicionar `contact_id`/`conversation_id` ao contexto bind (já viabiliza atribuição também nos logs `token_usage`).
- Persistir eventos de negócio na tabela `events` (além do log volátil).

### CRM (GHL)
- **Particularidade Adrian: usa Bearer API key** (`ghl/base.py`, header `Version: 2021-07-28`), **não PIT** como os outros agentes da frota. Documentar isso para o Hub/coletor — credencial e modelo de auth diferentes.
- Sem mudança funcional de CRM na Fase 1.

### Oportunidades (comercial — Fase 3)
- Adicionar mixin `ghl/opportunities.py`: criar/atualizar opportunity no pipeline ao qualificar (`qualificado_agendado` / `qualificado_sem_agenda`). Persistir `opportunity_id` em `sessions.state` e emitir evento. Único caminho para métrica de venda atribuível à IA.

---

## 4. ESTRATÉGIA DE INTEGRAÇÃO (como o Hub consome)

Adrian **já expõe** as interfaces — o coletor do Hub usa 3 canais complementares:

1. **Pull SQL — `token_usage` com FK** (após GAP-1): fonte canônica de custo por conversa/lead/componente. Coletor lê incremental por `id`/`created_at`. Idem `events` (operacional) e `sessions` (estado atual).
2. **Pull HTTP — `/usage`**: totais agregados BRL+tokens para visão rápida/reconciliação.
3. **Scrape Prometheus — `/metrics`**: o Hub roda Prometheus com **retenção** (resolve GAP-8: contadores in-memory passam a ter série temporal persistida fora do processo).

**Export/replicação SQLite (GAP-9):**
- Curto prazo: dump incremental — job `sqlite3 data/adrian.db .dump` ou `VACUUM INTO` agendado + envio ao Hub; ou Litestream (replicação contínua para object storage).
- Recomendado para o Hub: **endpoint de export incremental** `GET /export/usage?since=<id>` e `GET /export/events?since=<id>` (lê tabelas com FK, autenticado pelo webhook secret) → coletor faz pull periódico sem acessar o arquivo SQLite diretamente.
- Reforçar **GAP-11**: tornar webhook secret obrigatório antes de expor qualquer endpoint de export.

---

## 5. EVENTOS RECOMENDADOS (mapeados ao código real)

Tabela `events` append-only. Muitos já têm gancho — `side_effects` e métricas Prometheus já existem; falta persistir como evento e setar abandono.

| Evento | Onde emitir (código real) | Já existe base? |
|---|---|---|
| `CONVERSATION_STARTED` | `orchestrator.run_turn` ao `load_or_new` criar sessão nova / primeiro `replied` | `sessions` + `adrian_turns_total` |
| `CONVERSATION_COMPLETED` | quando `terminal_reason` é setado | ✅ `terminal_reason` cobre desfecho (`qualificado_*`, `handoff_*`) |
| `HANDOFF_CREATED` | `_escalate` → `encaminhar_para_vendedor` | ✅ `side_effects(escalation)` + `adrian_handoff_total` |
| `APPOINTMENT_CREATED` | passo 11 `crm.create_appointment` | ✅ `side_effects(booking)` + `adrian_qualificados_total` |
| `LLM_CALL` | `usage.record_usage` (com FK) | ✅ `token_usage` (após GAP-1) |
| `WHISPER_TRANSCRIPTION` | `audio/whisper.py` (após instrumentação) | ❌ criar (GAP-4) |
| `CONVERSATION_ABANDONED` | **novo** job de timeout que seta `terminal_reason = abandonado` | ⚠️ enum existe, nunca setado (GAP-7) |
| `FOLLOWUP_STARTED` / `FOLLOWUP_FINISHED` | **nova** lógica de re-engajamento | ❌ inexistente (GAP-7) |

> `terminal_reason` (`tools/terminal.py`) já cobre os desfechos; **abandono precisa ser efetivamente setado** por job de timeout (sem isso, `abandonado` permanece morto no enum).

---

## 6. PLANO DE EXECUÇÃO

### Fase 1 — Quick wins financeiros (foco #1, baixo risco)
1. **FK em `token_usage`** (`db/engine.py`): `contact_id`, `conversation_id`, `total_tokens`. *Adequação mais importante e mais barata.*
2. Propagar identidade via `structlog.contextvars` bind em `orchestrator.run_turn`; `usage.record_usage` lê e grava FK + `total_tokens`.
3. **Pricing confirmado** (`cost.py` + tabela `pricing`): substituir defaults placeholder por preços reais do billing OpenAI; manter USD→BRL.
4. **Whisper contabilizado** (`audio/whisper.py` → `record_usage("whisper", ...)`).
- *Resultado:* custo atribuível por conversa/lead e preciso. Hub passa a ler custo unitário.

### Fase 2 — Operacional e durabilidade
5. **Tabela `events`** append-only + emissão nos ganchos existentes (`_escalate`, booking, `terminal_reason`).
6. **Abandono/timeout:** job que varre `sessions` sem atividade > N e seta `terminal_reason = abandonado` + evento.
7. **Export SQLite:** endpoints `/export/usage` e `/export/events` incrementais (ou Litestream) + tornar webhook secret obrigatório.

### Fase 3 — Comercial e re-engajamento
8. **Opportunities GHL** (`ghl/opportunities.py`): criar/atualizar opportunity ao qualificar; persistir `opportunity_id`; evento. Fecha a cegueira comercial (GAP-5).
9. **Follow-up:** lógica de re-engajamento + eventos `FOLLOWUP_STARTED/FINISHED`.

---

## 7. RESUMO EXECUTIVO

Adrian já é o agente mais instrumentado da frota: grava tokens e custo em BRL por componente (`token_usage` + `cost.py`) e expõe `/usage` e `/metrics`. A adequação ao ZOI Performance Hub é cirúrgica, não estrutural. **A intervenção #1 — mais importante e mais barata — é adicionar `contact_id`/`conversation_id` a `token_usage`**, transformando custo agregado em custo por conversa/lead. Em seguida: preços confirmados com billing (precisão), `total_tokens` e Whisper (cobertura financeira completa). A Fase 2 dá durabilidade (tabela `events`, abandono efetivamente setado, export do SQLite e Prometheus com retenção no Hub). A Fase 3 abre a camada comercial (Opportunities GHL) e follow-up. Particularidade operacional para o Hub: Adrian autentica no GHL por **Bearer API key**, não PIT.

---

## 8. SCORE DE ADERÊNCIA

| Dimensão | Atual | Projetado (pós-plano) |
|---|---|---|
| Banco de Dados | 5 | 8 (events + FK + export) |
| Custos | 6 | 9 (FK + pricing confirmado + Whisper) |
| Tokens | 7 | 9 (FK + total_tokens + Whisper) |
| Operacional | 5 | 8 (abandono + events + timeout) |
| Comercial | 2 | 7 (Opportunities GHL) |
| Telemetria/Integração | 6 | 9 (Prometheus c/ retenção + export incremental) |
| Segurança | 4 | 7 (secret obrigatório) |

**Baseline global: ~5,4/10 (o mais alto da frota).**
**Projetado pós-plano: ~8,3/10.**
**Maior salto / menor custo: Fase 1 (FK custo↔conversa) — destrava custo unitário imediatamente.**

---

## ADENDO — Alinhamento de Frota v1 (decisões centrais, sobrepõem o acima)

### A. Contrato canônico de eventos
A tabela `events` projeta o envelope do `CONTRATO_EVENTOS_CANONICO.md` (v1): `event_id`, `schema_version=1`, `event_type`, `client="noxcar"`, `agent="adrian-noxcar"`, `contact_id`, `conversation_id`, `occurred_at` (UTC), `payload`. Adrian já é o mais alinhado (componentes `updater`/`inventory_expert`/`voice` ∈ vocabulário).

### B. Custo DUPLO em BRL (decisão da frota) — Adrian já está alinhado
Adrian JÁ calcula `cost_brl` localmente (vantagem única da frota). Ação: substituir preços placeholder por tabela `pricing` confirmada (já previsto, GAP-2) + adicionar `usd_brl_rate` e `pricing_version` ao payload de `LLM_CALL`/`WHISPER` + emitir tokens crus. Hub recalcula `hub_cost_brl` (reconciliação contra o `cost_brl` do agente).

### C. Integração — transporte PULL HTTP /export (concretiza o plano)
Confirmado o caminho do plano: endpoint `GET /export/events?since=<cursor>&secret=<hmac>` no envelope canônico (substitui `/export/usage` + `/export/events` separados por um único de eventos). Adapter: `hub/adapters/sqlite_export_adapter.py` (registry `adrian-noxcar`). GAP-11 (webhook secret obrigatório) é pré-requisito de segurança do endpoint.

### D. LGPD — adiado para pós-MVP
Sem tratamento de PII no MVP. Payload exportado = IDs + métricas, sem PII bruta. Dívida técnica pós-MVP.
