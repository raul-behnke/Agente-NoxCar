Você é o agente de engenharia responsável pelo repositório `agente-noxcar` (agente "Adrian", Nox Car), em produção na VPS 147.79.87.179 (adrian-nox.appzoi.com.br, systemd adrian.service).

CONTEXTO
A ZOI constrói o ZOI Performance Hub. Produza PLANO_ADEQUACAO_TELEMETRIA_NOXCAR.md adequando ESTE agente. Apenas o plano.

FATOS CONFIRMADOS
- Stack: Python + FastAPI, Gunicorn + Uvicorn (2 workers), :8123. Nginx TLS, systemd. VPS sem git.
- Banco: SQLite (data/adrian.db), sqlite3 stdlib puro + Agno SqliteDb (mesmo arquivo). SEM ORM. 4 tabelas próprias: sessions, processed_messages, side_effects, token_usage.
- IA: Pipeline determinístico 3 LLMs — Updater (OpenAI SDK direto) → EstoqueExpert (Agno) → Adrian voice (Agno). Modelo: gpt-5-mini (todos os 3 componentes). Whisper para áudio.
- CRM: GHL via Bearer API key (Version 2021-07-28). PARTICULARIDADE: você usa Bearer key, não PIT como os outros. Contacts/conversations/calendars/workflows/custom values. SEM Opportunities/Pipeline.
- Logs: structlog JSON (eventos nomeados: token_usage, handoff_failed, turn_unhandled_error). stdout → journald.
- Telemetria: Prometheus /metrics (adrian_turns_total, adrian_handoff_total, adrian_qualificados_total, adrian_llm_latency_seconds, adrian_tokens_total, adrian_cost_brl_total). Endpoints /usage e /health.

PARTICULARIDADE-CHAVE (você é o MAIS MADURO da frota — mas com 1 gap cirúrgico)
- VOCÊ JÁ TEM tabela `token_usage`: id, component, model, prompt_tokens, completion_tokens, cost_brl, created_at.
- VOCÊ JÁ CALCULA CUSTO EM BRL (cost.py): cost_usd = (prompt/1e6)*price_in + (completion/1e6)*price_out; cost_brl = cost_usd * usd_brl_rate. Via env: OPENAI_PRICE_IN_PER_1M (default 0.25), OPENAI_PRICE_OUT_PER_1M (default 2.00), USD_BRL_RATE (default 5.40).
- GAP CIRÚRGICO #1: `token_usage` NÃO TEM contact_id NEM conversation_id → custo NÃO é atribuível por conversa/lead (só total e por componente). Esta é sua adequação mais importante e mais barata: adicionar FK.
- GAP #2: preços são PLACEHOLDER (comentário no código: "confirmar com billing OpenAI") → cost_brl impreciso. Migrar para tabela pricing confirmada.
- GAP #3: total_tokens não armazenado (derivável de prompt+completion).
- GAP #4: Whisper não contabilizado em token_usage.

OUTROS GAPS
5. SEM Opportunities/Pipeline (opportunityId ausente em todo o código) → comercial cego.
6. Mensagens não persistidas localmente (só GHL).
7. Follow-up inexistente; `abandonado` está no enum mas NUNCA é setado; sem timeout.
8. Métricas Prometheus in-memory (resetam no restart).
9. SQLite local sem replicação.
10. sessions = snapshot sobrescrito (sem histórico de transições de estado).
11. webhook secret pode estar desabilitado.

O QUE O PLANO DEVE CONTER

1. SITUAÇÃO ATUAL — destacar token_usage + cost_brl como a base financeira mais avançada da frota; o problema é atribuição e precisão, não ausência.

2. GAPS — Operacional / Financeiro / Comercial.

3. ADEQUAÇÕES NECESSÁRIAS — Banco | Logs | Tokens | Custos | Whisper | CRM | Oportunidades.
   - Tokens: adicionar contact_id + conversation_id em token_usage (quick win #1); armazenar total_tokens; contabilizar Whisper.
   - Custos: substituir preços placeholder por tabela pricing confirmada com billing; manter conversão USD→BRL.
   - Banco: tabela events append-only com transições de estado.

4. ESTRATÉGIA DE INTEGRAÇÃO — SQLite local: definir export/replicação. Você já expõe /usage e /metrics — recomende como o coletor do Hub consome (ler token_usage com FK + /usage + scrape Prometheus com retenção).

5. EVENTOS RECOMENDADOS — mapear ao código (side_effects e métricas JÁ existem):
   CONVERSATION_STARTED, CONVERSATION_COMPLETED, HANDOFF_CREATED, APPOINTMENT_CREATED, FOLLOWUP_STARTED, FOLLOWUP_FINISHED, CONVERSATION_ABANDONED, LLM_CALL, WHISPER_TRANSCRIPTION. terminal_reason já cobre desfechos; abandono precisa ser efetivamente setado.

6. PLANO DE EXECUÇÃO — Fase 1 (FK contact_id em token_usage + total_tokens + Whisper + pricing confirmado), Fase 2 (events append-only + abandono/timeout + export SQLite), Fase 3 (Opportunities GHL + follow-up).

7. RESUMO EXECUTIVO.

8. SCORE DE ADERÊNCIA — atual (baseline ~5,4/10, o mais alto da frota) e projetado.

REGRA: cite arquivos/tabelas reais (db/engine.py, cost.py, token_usage, metrics.py). O foco #1 é a FK de custo↔conversa. Nada genérico.
