# RELATÓRIO TÉCNICO — AGENTE ADRIAN (NOX CAR)
> Análise para integração ao Dashboard Centralizado de Performance de Agentes — ZOI
> Base analisada: `/opt/adrian` (clonado da VPS em `opt-adrian/`). Coletado em 2026-06-17.

---

# 1. IDENTIFICAÇÃO DO PROJETO

| Campo | Valor |
|---|---|
| **Nome do Projeto** | Adrian — Nox Car Pre-Attendance Agent (Agno) |
| **Cliente** | Nox Car (revenda / concessionária de veículos) |
| **Versão** | Sem string de versão formal. README marca **"V1"** (inventário inteiro no prompt, ~60 veículos). Sem versionamento git na VPS. |
| **Ambiente** | Produção — VPS 147.79.87.179, `adrian-nox.appzoi.com.br`, systemd `adrian.service` (active/running) |
| **Repositório** | `/opt/adrian` na VPS. **SEM git** (sem `.git`, remote vazio). Cópia local via rsync. |
| **Responsável Técnico** | NÃO IDENTIFICADO (não consta no código/README) |

**Resumo Executivo:** SDR de pré-atendimento comercial para leads inbound de revenda automotiva (WhatsApp via GoHighLevel/LeadConnector). Orquestrador determinístico (FastAPI) envolve pipeline de 3 LLMs: **Updater** (extração estruturada) → **EstoqueExpert** (seleção de estoque) → **Adrian voice** (geração da fala). Qualifica o lead por um funil de 9 campos, oferece agendamento de visita, e escala para vendedor humano quando qualificado/solicitado/erro. Regras de negócio vivem em código, não em prompt.

---

# 2. STACK TECNOLÓGICA

| Camada | Tecnologia |
|---|---|
| **Backend** | Python, FastAPI (`app:api`), Gunicorn + Uvicorn worker (2 workers), bind `127.0.0.1:8123` |
| **Frontend** | NÃO EXISTE (apenas API/webhook; AgentOS local para dev/observabilidade) |
| **Banco de Dados** | SQLite (`data/adrian.db`), `sqlite3` stdlib puro + Agno `SqliteDb` (mesmo arquivo) |
| **ORM** | NÃO HÁ ORM. SQL cru via `sqlite3`. Pydantic para schemas de estado (não persistência relacional) |
| **Framework de IA** | Agno (Agent/AgentOS) para voice + EstoqueExpert; OpenAI SDK direto para Updater |
| **LLM Provider** | **OpenAI** (em todos os 3 componentes) |
| **Modelos Utilizados** | `gpt-5-mini` (default `settings.model_id`, env `ADRIAN_MODEL_ID`). Confirmado nos dados vivos: updater, inventory_expert, voice — todos `gpt-5-mini` |
| **Serviços Externos** | GoHighLevel / LeadConnector (`services.leadconnectorhq.com`), OpenAI, Whisper (transcrição de áudio) |
| **Infraestrutura** | Nginx (TLS Let's Encrypt, 80→443, `proxy_pass 127.0.0.1:8123`, body 10m, read_timeout 120s), systemd |
| **Hospedagem** | VPS própria (Hostinger-class), `147.79.87.179`, root |
| **Filas** | NÃO HÁ fila externa. Background via `asyncio.create_task` (fire-and-forget) + preempção por `contact_id` |
| **Cache** | `TTLCache` in-process (300s) para estoque e FAQ (custom values do GHL). Não-persistente, não thread-safe |
| **Storage** | SQLite local; fotos/áudio referenciados por URL (servidos pelo GHL, não armazenados) |

---

# 3. ARQUITETURA GERAL

**Fluxo completo de atendimento:**

1. **Entrada** — GHL chama `POST /webhook/inbound` a cada mensagem do lead. `?secret=` valida (HMAC timing-safe; se secret não configurado, auth desabilitada). `extract_payload` parseia (com fallbacks camelCase/snake_case). Gate de tag de ativação (`agente-ia`): sem tag → `ignored`.
2. **Pré-processamento** — áudio transcrito via Whisper; burst de mensagens recentes agregado (`aggregate_burst`); retorno imediato `{"action":"accepted"}` e processamento em background (GHL faz timeout em 60s).
3. **Processamento** (`run_turn`, pipeline 16 passos): gate de tag → dedup (`processed_messages`) → carrega sessão → guarda de estado terminal → lê histórico no GHL → **Updater (LLM#1)** extrai estado → exceções de escalação imediata (pediu humano / conflito) → merge → booking se escolheu slot → **Question Planner (código)** decide próxima pergunta → **EstoqueExpert (LLM#2)** + **Adrian voice (LLM#3)** geram resposta.
4. **Resposta** — fotos + bubbles enviadas via `POST /conversations/messages` (type SMS).
5. **Persistência** — sessão (`sessions`), dedup (`processed_messages`), idempotência (`side_effects`), tokens/custo (`token_usage`).
6. **Integrações** — leitura/escrita GHL (contatos, conversas, calendário, custom values, workflows); OpenAI; Whisper.

**Diagrama textual:**
```
WhatsApp (lead)
   ↓
GoHighLevel (CRM)  ──── webhook ───►  POST /webhook/inbound (FastAPI)
   ↑                                       ↓
   │                              extract_payload + gate tag
   │                                       ↓
   │                              Whisper (áudio→texto)
   │                                       ↓
   │                              run_turn (orquestrador 16 passos)
   │                                       ↓
   │                    ┌──── Updater (OpenAI gpt-5-mini) extrai estado
   │                    ├──── Question Planner (código)
   │                    ├──── EstoqueExpert (OpenAI) seleciona estoque
   │                    └──── Adrian voice (OpenAI) gera bubbles
   │                                       ↓
   │                              SQLite (sessions, token_usage, side_effects)
   │                                       ↓
   └──── send_message / create_appointment / add_note / workflow / remove_tag
```

---

# 4. BANCO DE DADOS

| Campo | Valor |
|---|---|
| **Tipo** | SQLite (arquivo único) |
| **Host** | Local na VPS — `data/adrian.db` (path via `ADRIAN_DB_FILE`, default `adrian.db`) |
| **Quantidade de tabelas** | **4** próprias (+ `sqlite_sequence` interna). Agno pode criar tabelas próprias no mesmo arquivo, mas não há schema dessas tabelas no código analisado. |

**Tabelas (definidas em `db/engine.py`):**

### `sessions`
- **Finalidade:** Estado da sessão de qualificação por contato (snapshot JSON).
- **Campos:** `contact_id TEXT PK`, `state TEXT NOT NULL` (SessionState serializado em JSON), `terminal_reason TEXT`, `updated_at TEXT`.
- **Relacionamentos:** Chaveada por `contact_id` (mesmo id do GHL). Sem FK formal.

### `processed_messages`
- **Finalidade:** Deduplicação (idempotência de mensagem).
- **Campos:** `message_id TEXT PK`, `processed_at TEXT`.
- **Relacionamentos:** nenhum.

### `side_effects`
- **Finalidade:** Idempotência de efeitos colaterais (escalação / agendamento únicos por conversa).
- **Campos:** `conversation_id TEXT`, `kind TEXT` (`'escalation' | 'booking'`), `created_at TEXT`, PK `(conversation_id, kind)`.
- **Relacionamentos:** `conversation_id` (geralmente = `contact_id`).

### `token_usage`
- **Finalidade:** Trilha de auditoria de tokens e custo por chamada LLM.
- **Campos:** `id INTEGER PK AUTOINCREMENT`, `component TEXT`, `model TEXT`, `prompt_tokens INTEGER`, `completion_tokens INTEGER`, `cost_brl REAL`, `created_at TEXT`.
- **Relacionamentos:** nenhum (NÃO há `contact_id`/`conversation_id` — custo não é atribuível a conversa).

**Mapa de armazenamento:**

| Dado | Tabela | Observação |
|---|---|---|
| Conversas | ⚠️ Parcial | Não há tabela de conversa própria. Histórico vive no GHL; `sessions` guarda só o estado agregado. |
| Mensagens | ❌ NÃO armazenadas localmente | Lidas do GHL on-demand (`get_history`). |
| Leads | `sessions` (`state` JSON: `collected.nome` etc.) | Dado canônico do lead está no GHL. |
| Contatos | `sessions.contact_id` | Canônico no GHL. |
| Atendimentos | `sessions` (`stage`, `terminal_reason`) | |
| Agendamentos | `side_effects` (kind=booking) + `sessions.state.appointment` | Agendamento real criado no calendário GHL. |
| Logs | ❌ não em tabela | structlog JSON (stdout / journald). |
| Tokens | `token_usage` | ✅ |
| Custos | `token_usage.cost_brl` | ✅ (estimado, ver §7) |

---

# 5. CONVERSAS E ATENDIMENTOS

**Como uma conversa é criada:** `db.sessions.load_or_new(contact_id, conversation_id)` em `run_turn`. Nova `SessionState` inicia `stage="novo_contato"`, `collected` vazio. Disparada pelo webhook (`/webhook/inbound`) ou pelo `greet` (lead novo de marketplace).

**Como uma conversa é encerrada:** Setando `state.terminal_reason`. Turnos seguintes curto-circuitam (`ignored — sessão encerrada`). Encerramentos "acionáveis" (`ACTIONABLE_TERMINALS`) disparam nota consolidada + workflow + remoção da tag.

**Campos utilizados:**

| Campo | Status |
|---|---|
| `conversationId` | ✅ Existe. Parseado do payload (`conversation_id`/`conversationId`), com **fallback = `contact_id`**. Resolvido no GHL via `/conversations/search` para ler histórico. Usado localmente para idempotência. |
| `contactId` | ✅ Identificador primário em todo o sistema. |
| `locationId` | ✅ `settings.crm_location_id` (config/env). |
| `opportunityId` | ❌ **NÃO IDENTIFICADO** — não existe em nenhum lugar do código. |

**Critérios de cada situação (`orchestrator.run_turn`):**

| Situação | Como identificar (evento/registro) |
|---|---|
| **Atendimento iniciado** | `Result("replied")` / criação de `sessions` row / métrica `adrian_turns_total{action="replied"}`. Saudação: `saudacao_feita` vira True. |
| **Atendimento concluído** | `terminal_reason` setado em `sessions`. Valores: `qualificado_agendado`, `qualificado_sem_agenda`, `handoff_solicitado`, `handoff_erro`, `inativo_sem_tag`, `abandonado`. |
| **Handoff** | `terminal_reason ∈ {handoff_solicitado, handoff_erro}` + métrica `adrian_handoff_total{reason}` + `side_effects(kind=escalation)`. Gatilhos: `pediu_humano`, `conflito`, ou falha de integração. |
| **Agendamento** | `update.chosen_slot_iso` → `create_appointment` → `terminal_reason=qualificado_agendado`, `stage=agendamento_criado`, `side_effects(kind=booking)`, `adrian_qualificados_total`. |
| **Follow-up** | ❌ **NÃO EXISTE** lógica de follow-up/timeout/re-engajamento no código. `abandonado` está no enum mas **nunca é setado**. |
| **Encerramento** | Vide "concluído". `inativo_sem_tag` quando lead perde a tag de ativação. |

---

# 6. INTEGRAÇÃO COM GHL / CRM

| Campo | Valor |
|---|---|
| **Integração** | GoHighLevel / LeadConnector REST API v2 (`services.leadconnectorhq.com`) |
| **OAuth ou API Key** | **API Key / Bearer token** (`Authorization: Bearer {CRM_API_KEY}`, header `Version: 2021-07-28`). NÃO usa OAuth. |

**Endpoints utilizados (TODOS):**

| # | Método | Path | Função | Recurso |
|---|---|---|---|---|
| 1 | GET | `/contacts/{id}` | `get_contact_tags` (lê tags) | Contatos |
| 2 | POST | `/contacts/{id}/notes` | `add_note` (body) | Contatos |
| 3 | DELETE | `/contacts/{id}/tags` | `remove_tag` (`{"tags":[...]}`) | Tags |
| 4 | GET | `/conversations/search` | `resolve_conversation_id` (`locationId`+`contactId`) | Conversas |
| 5 | GET | `/conversations/{conv}/messages` | `get_history` | Conversas/Mensagens |
| 6 | POST | `/conversations/messages` | `send_message` (`type:SMS`, contactId, message) | Conversas |
| 7 | POST | `/conversations/messages` | `send_attachment` (attachments URL) | Conversas |
| 8 | GET | `/calendars/{cal}/free-slots` | `get_free_slots` (startDate/endDate epoch ms) | Calendários |
| 9 | POST | `/calendars/events/appointments` | `create_appointment` (confirmed) | Calendários |
| 10 | GET | `/locations/{loc}/customValues/{cv}` | estoque + FAQ (cache 5min) | Custom Values |
| 11 | POST | `/contacts/{id}/workflow/{wf}` | `add_to_workflow` (escalação) | Workflows |

**Chamadas por recurso:**

| Recurso | Existe? | Detalhe |
|---|---|---|
| Contatos | ✅ | ler tags, add nota, remover tag |
| Conversas | ✅ | buscar, ler histórico, enviar msg/anexo |
| **Oportunidades / Pipelines** | ❌ **NÃO EXISTE** | Nenhum endpoint de opportunity/pipeline/stage |
| Calendários | ✅ | free-slots + criar agendamento |
| Custom Fields | ⚠️ Parcial | Lê **Custom Values** (estoque/FAQ). Não lê/escreve custom fields de contato |
| Tags | ✅ | lê (1), remove (3). **Não há add-tag** |
| Workflows | ✅ | adiciona contato ao workflow de escalação |

**Dados enviados/recebidos:**
- **Enviado:** mensagens (SMS), anexos (URLs de fotos), notas consolidadas (resumo do pré-atendimento), agendamentos (calendarId/locationId/contactId/start/end/confirmed), entrada em workflow, remoção de tag.
- **Recebido:** tags do contato, histórico de mensagens, slots livres, custom values (estoque JSON + FAQ YAML), objeto de agendamento criado.
- **Provenance:** `contactId` sempre vem do caller; `conversationId` derivado via search (ou = contactId); `locationId` da config. `opportunityId` ausente.

---

# 7. TOKENS E CUSTOS OPENAI

**Os tokens são armazenados?** ✅ **SIM**

| Campo | Valor |
|---|---|
| **Onde** | SQLite + Prometheus + log estruturado |
| **Tabela** | `token_usage` |
| **Campos** | `component`, `model`, `prompt_tokens`, `completion_tokens`, `cost_brl`, `created_at` |

**Mapeamento dos tokens:**

| Pedido | No sistema |
|---|---|
| `input_tokens` | `prompt_tokens` |
| `output_tokens` | `completion_tokens` |
| `total_tokens` | ❌ **NÃO armazenado** (derivável: `prompt + completion`) |

- **Modelo utilizado:** `gpt-5-mini` (campo `model` por linha). Componentes: `updater`, `inventory_expert`, `voice`.
- **Preço configurado:** via env — `OPENAI_PRICE_IN_PER_1M` (default **0.25 USD**/1M), `OPENAI_PRICE_OUT_PER_1M` (default **2.00 USD**/1M), `USD_BRL_RATE` (default **5.40**). ⚠️ Comentário no código marca os defaults como **PLACEHOLDER** ("confirmar com billing OpenAI").

**Existe cálculo de custo?** ✅ SIM (`cost.py`).

**Fórmula encontrada:**
```
cost_usd = (prompt_tokens / 1_000_000) × price_input_usd_per_1m
         + (completion_tokens / 1_000_000) × price_output_usd_per_1m

cost_brl = cost_usd × usd_brl_rate
```
Com defaults: `cost_brl = ((prompt/1e6)×0.25 + (completion/1e6)×2.00) × 5.40`. Armazenado em BRL na coluna `cost_brl`.

**Dados vivos (auditoria atual):**

| component | model | chamadas | prompt | completion | cost_brl |
|---|---|---|---|---|---|
| inventory_expert | gpt-5-mini | 6 | 26.601 | 9.765 | 0,1414 |
| updater | gpt-5-mini | 10 | 16.542 | 15.367 | 0,1883 |
| voice | gpt-5-mini | 8 | 35.325 | 11.642 | 0,1734 |

⚠️ Limitação crítica: `token_usage` **não tem `contact_id`/`conversation_id`** → custo NÃO é atribuível por conversa/lead (apenas totais e por componente).

---

# 8. LOGS E TELEMETRIA

| Item | Valor |
|---|---|
| **Logs existentes** | Log estruturado JSON via **structlog** (fallback stdlib logging) |
| **Sistema de logs** | structlog (`obs.py`), eventos nomeados (`token_usage`, `handoff_failed`, `turn_unhandled_error`, etc.) |
| **Arquivos** | Sem arquivo dedicado no código → stdout → capturado por systemd/journald na VPS |
| **Tabelas** | `token_usage` (custo), `processed_messages` (dedup), `side_effects` (efeitos) |
| **Ferramentas externas** | Prometheus (endpoint `/metrics`); AgentOS (tracing local, dev). Sem APM/Sentry/Datadog |

**Métricas Prometheus definidas (`metrics.py`):**
- `adrian_turns_total{action}`, `adrian_handoff_total{reason}`, `adrian_qualificados_total{outcome}`, `adrian_llm_latency_seconds{component}`, `adrian_tokens_total{component,kind}`, `adrian_cost_brl_total{component}`.

| Pergunta | Resposta | Localização | Estrutura / Campos |
|---|---|---|---|
| Histórico de mensagens? | ⚠️ Sim, mas **no GHL** (não local) | GHL `/conversations/{id}/messages` | direction, body, type |
| Histórico de execução? | ⚠️ Parcial | Prometheus `adrian_turns_total` + logs | action, contact_id (em logs) |
| Histórico de erros? | ✅ Sim (logs) | structlog stdout/journald | event, contact_id, error |
| Histórico de chamadas OpenAI? | ✅ Sim | `token_usage` + `adrian_tokens_total` + log `token_usage` | component, model, prompt/completion tokens, cost_brl, created_at |

---

# 9. MÉTRICAS POSSÍVEIS (sem alterar código)

## Operacionais

| Métrica | Disponível | Fonte | Confiabilidade |
|---|---|---|---|
| Conversas iniciadas | SIM | `adrian_turns_total` / rows em `sessions` | Média (sessão ≈ contato, não conversa GHL) |
| Conversas concluídas | SIM | `sessions.terminal_reason` not null | Alta |
| Handoffs | SIM | `adrian_handoff_total{reason}` / `side_effects(escalation)` | Alta |
| Follow-ups | **NÃO** | Não existe lógica de follow-up | — |
| Agendamentos | SIM | `side_effects(booking)` / `adrian_qualificados_total{qualificado_agendado}` | Alta |
| Abandono | **NÃO** | `abandonado` nunca é setado; sem timeout | Baixa |

## Financeiras

| Métrica | Disponível | Fonte | Confiabilidade |
|---|---|---|---|
| Tokens | SIM | `token_usage` / `adrian_tokens_total` | Alta |
| Custos OpenAI | SIM (estimado) | `token_usage.cost_brl` | Média (preços são placeholder) |
| Custo por conversa | **NÃO** | `token_usage` sem `contact_id`/`conversation_id` | — |
| Custo por mensagem | **NÃO** | idem (custo por componente apenas) | — |

## Comerciais

| Métrica | Disponível | Fonte | Confiabilidade |
|---|---|---|---|
| Oportunidades criadas | **NÃO** | Sem integração de opportunity/pipeline | — |
| Oportunidades atualizadas | **NÃO** | idem | — |
| Vendas atribuíveis à IA | **NÃO** | Sem ligação a opportunity/venda; só nota+workflow | — |
| (Proxy) Qualificados | SIM | `adrian_qualificados_total` | Alta |

---

# 10. GAPS PARA O DASHBOARD DE PERFORMANCE

| GAP | Impacto | Criticidade |
|---|---|---|
| `token_usage` sem `contact_id`/`conversation_id` | Impossível custo por conversa/lead/mensagem | **Alta** |
| Sem integração de Oportunidades/Pipeline (`opportunityId` ausente) | Sem métricas comerciais / atribuição de venda à IA | **Alta** |
| Mensagens não persistidas localmente (só GHL) | Dashboard depende da API GHL para histórico; sem fonte única | **Alta** |
| Sem follow-up / `abandonado` nunca setado | "Abandono" e "follow-up" não mensuráveis | Média |
| Métricas Prometheus são contadores in-memory (resetam no restart) | Sem série temporal persistida sem scraper externo | Média |
| `total_tokens` não armazenado | Derivável, mas exige cálculo | Baixa |
| Preços OpenAI são placeholders (0.25 / 2.00 / 5.40) | Custo em BRL impreciso até confirmar billing | Média |
| Logs em stdout/journald sem coletor estruturado | Sem query histórica de execução/erros sem ELK/Loki | Média |
| SQLite local (sem replicação) | Risco de perda; difícil consolidar multi-agente | Média |
| `sessions` = snapshot (sobrescrito) | Sem histórico de transições de estado/funil | Média |
| Sem `secret` no `greet` + secret pode estar desabilitado | Telemetria pode incluir tráfego não autenticado | Baixa |

---

# 11. RECOMENDAÇÕES DE INSTRUMENTAÇÃO

Eventos a emitir futuramente (idealmente em tabela `events` append-only com `contact_id`, `conversation_id`, `timestamp`):

| Evento | Motivo | Dados necessários | Complexidade |
|---|---|---|---|
| `AGENT_STARTED` | Marcar início de turno/sessão | contact_id, conversation_id, ts | Baixa |
| `MESSAGE_RECEIVED` | Volume inbound, tempo resposta | contact_id, message_id, ts, canal | Baixa |
| `MESSAGE_SENT` | Volume outbound, bubbles enviadas | contact_id, ts, n_bubbles | Baixa |
| `LLM_CALL` | Custo por conversa (corrigir gap #1) | contact_id, component, model, tokens, cost_brl | Baixa (adicionar FK em `token_usage`) |
| `FIELD_COLLECTED` | Progresso do funil de qualificação | contact_id, field, valor | Média |
| `HANDOFF_CREATED` | Taxa/motivo de escalação | contact_id, reason, motivo | Baixa (já há `side_effects`) |
| `APPOINTMENT_CREATED` | Conversão de agendamento | contact_id, slot_iso, veiculo | Baixa |
| `FOLLOWUP_STARTED` / `FOLLOWUP_FINISHED` | Habilitar métrica de follow-up (não existe hoje) | contact_id, motivo, ts | Alta (lógica nova) |
| `CONVERSATION_CLOSED` | Desfecho + duração | contact_id, terminal_reason, duração | Baixa |
| `OPPORTUNITY_LINKED` | Atribuição comercial/venda à IA | contact_id, opportunityId, pipeline_stage | Alta (nova integração GHL) |

---

# 12. SCORE DE OBSERVABILIDADE (0–10)

| Dimensão | Nota | Justificativa |
|---|---|---|
| Banco de Dados | 5 | SQLite funcional, 4 tabelas claras; mas snapshot (sem histórico), sem FK custo↔conversa, local |
| Logs | 6 | structlog JSON com eventos nomeados; sem coletor/persistência consultável |
| Custos | 6 | Calculados e gravados em BRL por componente; preços placeholder + não atribuível por conversa |
| CRM | 6 | Integração sólida (contatos/conversas/calendário/workflow); sem oportunidades/pipeline |
| Conversas | 5 | Estado por contato persistido; mensagens só no GHL; sem `opportunityId` |
| Telemetria | 6 | Prometheus completo (turns/handoff/qualificados/tokens/custo/latência); contadores voláteis |
| Monitoramento | 4 | `/health`, `/metrics`, `/usage`, smoke script; sem alertas/APM/dashboards |

**Nota Final: 5,4 / 10**

**Justificativa:** Fundação de telemetria acima da média para um agente desta fase — tokens e custo são gravados, métricas Prometheus cobrem os desfechos chave, e o orquestrador determinístico torna estados auditáveis. Os limitadores são: custo não atribuível por conversa, ausência total de camada comercial (oportunidades/vendas), mensagens não persistidas localmente, e métricas voláteis. Bom para dashboards operacionais e financeiros agregados; insuficiente para análise comercial e custo unitário sem instrumentação adicional.

---

# 13. RESUMO EXECUTIVO FINAL

O Adrian é um SDR de pré-atendimento (Nox Car) que recebe mensagens de leads via GoHighLevel num webhook FastAPI, transcreve áudio (Whisper), e roda um pipeline determinístico de 3 LLMs OpenAI (`gpt-5-mini`): extrai estado, planeja a próxima pergunta do funil de 9 campos, seleciona estoque e gera a fala. Qualifica o lead, oferece agendamento de visita (calendário GHL) e escala para vendedor humano quando solicitado, em conflito, qualificado, ou por falha de integração. Estado por contato fica em SQLite; histórico de mensagens fica no GHL.

**Já pode ser medido:** conversas iniciadas/concluídas, desfechos (qualificado agendado/sem agenda, handoff solicitado/erro), agendamentos, handoffs por motivo, tokens (input/output) e custo estimado em BRL por componente, latência de LLM.

**Não pode ser medido:** custo por conversa/lead/mensagem (falta `contact_id` em `token_usage`); métricas comerciais (oportunidades, vendas atribuíveis — sem integração de pipeline); follow-up e abandono (lógica inexistente); histórico de transições de estado.

**Principais riscos:** SQLite local sem replicação; preços OpenAI placeholder (custo BRL impreciso); métricas Prometheus voláteis (resetam no restart); webhook secret pode estar desabilitado; sem versionamento git na VPS.

**Principais oportunidades:** adicionar `contact_id`/`conversation_id` em `token_usage` (ganho imediato de custo unitário); tabela `events` append-only; integração de Oportunidades GHL para fechar o ciclo comercial; scraper Prometheus → série temporal.

**Esforço estimado de integração ao Dashboard:** Médio. Métricas operacionais e financeiras agregadas saem hoje via `/metrics` e `/usage` + leitura direta do `token_usage`/`sessions`. Custo por conversa e camada comercial exigem instrumentação (≈ baixo-médio para a FK de custo; alto para oportunidades/vendas e follow-up).

**Classificação Final: 🟡 Requer Ajustes**
