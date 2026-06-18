Você é o agente de engenharia do repositório `agente-noxcar` (Adrian, Nox Car).

CONTEXTO
Adequação (Fases 1-3) já APLICADA — o agente mais alinhado da frota. Validação central achou apenas gaps de envelope + segurança vs CONTRATO CANÔNICO v1. Rodada de CORREÇÃO (v2). Referência: `/Users/raulbehnke/var-zoi/CONTRATO_EVENTOS_CANONICO.md`. Atualize código + `RELATORIO_ADEQUACAO_APLICADA.md`.

O QUE JÁ ESTÁ CERTO (não regredir)
- FK custo↔conversa (`token_usage` com `contact_id`/`conversation_id`/`total_tokens`), **custo em BRL** (cost.py — único da frota já alinhado), Whisper verbose_json, tabela `events`, abandono/sweep, Opportunities (`OPPORTUNITY_CREATED` ✅ canônico), follow-up, **endpoint `/export/events` + `/export/usage`** (transporte correto). Manter tudo.

GAPS A CORRIGIR (bloqueiam integração)

1. ENVELOPE CANÔNICO incompleto. A tabela `events` (e o que `/export/events` serializa) NÃO tem `event_id`, `schema_version`, `client`, `agent`. Adicionar:
   - `event_id` TEXT UNIQUE NOT NULL = `uuid4()` (idempotência — o adapter do Hub deduplica por este campo; sem ele, retry duplica).
   - `schema_version` INTEGER DEFAULT 1.
   - `client` TEXT = `"noxcar"`.
   - `agent` TEXT = `"adrian-noxcar"`.
   Preencher em `db/events.py::record_event` e na serialização de `db/events.py::events_since` / `endpoints/export.py`. Migração idempotente PRAGMA-guard (mesmo padrão do `_migrate()` existente).
   - Garantir que o payload de `LLM_CALL` (servido via `token_usage`/`/export/usage`) também carregue `event_id` + `cost_brl` + `usd_brl_rate` + `pricing_version` para o Hub reconciliar.

2. SEGURANÇA do export. `GET /export/*` e webhook ficam abertos se `ADRIAN_WEBHOOK_SECRET` não setado (GAP-11). Tornar o secret OBRIGATÓRIO para os endpoints de export (recusar 401 se ausente) — não basta `require_secret` retornar True sem secret.

3. `usd_brl_rate` + `pricing_version` no payload. O custo BRL já é calculado; garantir que `usd_brl_rate` e `pricing_version` (versão da linha `pricing` usada) acompanhem cada `LLM_CALL`/`WHISPER` exportado, para reconciliação com o `hub_cost_brl`.

ENTREGÁVEL
- Código + suíte verde (110 passed mantido; zero regressão). Validar: evento com `event_id` único + `client`/`agent`/`schema_version`; `/export/events` recusa sem secret; payload de custo traz `cost_brl`+`usd_brl_rate`+`pricing_version`.
- Atualizar relatório: seção "Correções v2" — envelope, secret obrigatório, reconciliação.

REGRA: cite arquivos reais (db/events.py, db/engine.py, endpoints/export.py, security.py, cost.py). Nada genérico — Adrian já está perto, foco cirúrgico.
