# PLANO — Modo Atendimento Fora-do-Horário (Agente NOX / Adrian)

> Objetivo: atender leads **fora do horário comercial** de forma natural e objetiva,
> **apenas qualificando** (a IA NÃO insiste em fechar/agendar), coletando os dados
> necessários para o vendedor continuar a negociação no horário comercial.
> Notificação consolidada ao vendedor com os dados + contato.

Base: pipeline determinístico de 3 estágios já existente
(`Updater extract → merge → Question Planner → Team[voice] → send`).
Regras duras em Python, não no prompt. PT-BR. TZ `America/Sao_Paulo`.

---

## 1. Decisões fechadas (grill-me, 13 pontos)

| # | Decisão |
|---|---------|
| 1 | Modo fora-horário **suprime agendamento**: qualifica só e encerra em handoff. Dentro do horário = fluxo atual + cidade. |
| 2 | Detecção por **relógio, re-avaliada a cada mensagem**. TZ `America/Sao_Paulo`. **Sem feriados** (v1). |
| 3 | **Horário aberto (fonte-da-verdade)**: Seg–sex 08:00–18:30 · Sáb 09:00–13:00 · Dom fechado. **IA ativa = complemento** desse horário. |
| 4 | **Reescrita do Question Planner** para seguir a ordem exata do roteiro, reusando a lógica condicional já testada. |
| 5 | Campo **`cidade` coletado SEMPRE** (dentro e fora do horário). |
| 6 | Ordem do funil: foco → `possui_troca`→subfields → `possui_entrada`→valor → `metodo_negociacao` → `cidade` → agendamento (**só dentro do horário**). |
| 7 | **`possui_entrada`** vira gate booleano; resposta "ainda não defini" → tratada como `skipped_fields` (não re-pergunta). |
| 8 | **Modelo ortogonal**: troca e entrada são **flags independentes**; `metodo_negociacao` = mecanismo de funding core; `faixa_parcela` só se financiamento/100. |
| 9 | Timeout de 1h → **fora de escopo** (aplicado direto no CRM). Sem sweep novo. |
| 10 | **Saudação condicional por modo** + envio de **vídeo (URL em settings)**, 1x. |
| 11 | **Nota ao vendedor**: inclui cidade + 3 eixos; **notifica sempre no encerramento** (parcial incluso). |
| 12 | Novo terminal reason **`qualificado_fora_horario`** + **variante de persona** ("não insistir em fechar"). |
| 13 | **`agent/hours.py`** puro; horários em `settings`; `now` injetável; modo passado como **param explícito** no pipeline (sem flag no `SessionState`). |

---

## 2. Modelo de dados (eixos ortogonais)

Negociação real é combinatória. Modelada em 3 eixos independentes:

- `possui_troca: bool | None` — eixo troca (gate para subfields).
- `possui_entrada: bool | None` — eixo entrada (gate para `valor_entrada`).
- `metodo_negociacao: MetodoNegociacao | None` — funding core: `financiamento` / `avista` / `consorcio` / `financiamento_100` / `troca` (troca integral).

Qualquer combinação registra naturalmente:

| Cliente quer | Registro |
|---|---|
| Troca + Financiamento | `possui_troca=True` + `metodo=financiamento` |
| Troca + Entrada + Financiamento | `possui_troca=True` + `possui_entrada=True` + `metodo=financiamento` |
| Só troca (cobre tudo) | `possui_troca=True` + `metodo=troca` |
| Entrada + À vista | `possui_entrada=True` + `metodo=avista` |

`combinacao` no enum: **mantido só para retrocompat**; o planner NÃO usa mais `metodo` para dirigir troca/entrada (os gates `possui_*` cobrem).

Mapeamento das opções do roteiro → enum:

| Roteiro | Enum |
|---|---|
| Financiamento | `financiamento` |
| Financiamento + entrada | `possui_entrada=True` + `metodo=financiamento` |
| À vista | `avista` |
| 100% | `financiamento_100` |
| Consórcio | `consorcio` |
| Ainda avaliando | `None` → skip |

`faixa_parcela` perguntado se `metodo in (financiamento, financiamento_100)`.

---

## 3. Horário de funcionamento

Loja **aberta**:

```
Segunda   08:00–18:30
Terça     08:00–18:30
Quarta    08:00–18:30
Quinta    08:00–18:30
Sexta     08:00–18:30
Sábado    09:00–13:00
Domingo   fechado
```

**IA ativa (fora-do-horário) = complemento**: seg 18:30 → 08:00, sáb após 13:00 + antes 09:00,
domingo o dia inteiro. A borda 07:30–08:00 (dia útil) fica coberta pela IA sem conflito.

---

## 4. Fluxo fora-do-horário (roteiro → estado)

1. **Saudação** — texto "fora do horário" + vídeo da estrutura + confirma o carro de interesse.
2. **Confirmar interesse** → `veiculo_interesse_confirmado` (intent `foco`).
3. **Troca?** → `possui_troca`. Se sim: `troca.modelo`, `troca.ano`, `troca.km` (`troca.quitado` opcional).
4. **Entrada?** → `possui_entrada`. Se sim: `valor_entrada`. "Ainda não defini" → skip.
5. **Forma de pagamento** → `metodo_negociacao`. Se financiamento/100: `faixa_parcela`.
6. **Cidade** → `cidade`.
7. **Encerramento** — mensagem de fechamento (sem agendamento) + handoff → terminal `qualificado_fora_horario`.

---

## 5. Mudanças de código

### `config/settings.py`
- `BUSINESS_HOURS`: dict `dia_semana → (abre, fecha)` (ou `None` = fechado). Ajustável sem deploy de código.
- `AFTERHOURS_VIDEO_URL` (env `URL_VIDEO`), default:
  `https://assets.cdn.filesafe.space/wbSVxrr4mvYNRaIw1eJ7/media/6a452aaab653a0ddc229a739.mp4`

### `agent/hours.py` (novo)
- `is_after_hours(now: datetime | None = None) -> bool` — puro, usa `ZoneInfo(settings.app_timezone)`,
  consulta `settings.BUSINESS_HOURS`. `now` default = agora (injetável p/ teste).

### `agent/schemas.py`
- `Collected`: **+** `cidade: Optional[str]`, `possui_entrada: Optional[bool]`, `faixa_parcela: Optional[str]`.
- `TrocaInfo`: **remove** `restante`; `is_complete()` = `modelo/ano/km` (quitado **opcional**, não trava funil).
- `MetodoNegociacao`: mantém enum (retrocompat).
- `PRIORITY_FIELDS`: reordenado conforme roteiro (§4).
- `compute_missing()`:
  - gates: `possui_troca is True` → troca subfields; `possui_entrada is True` → `valor_entrada`.
  - `faixa_parcela` só se `metodo in (financiamento, financiamento_100)`.
  - `cidade` **sempre** exigida.
  - `_troca_relevant` passa a depender de `possui_troca` (não do `metodo`).

### `agent/question_planner.py`
- `CANONICAL_QUESTIONS`: **+** `cidade`, `possui_troca`, `possui_entrada`, `faixa_parcela`; ajusta bloco troca (sem `restante`).
- `_TROCA_SUBFIELDS`: remove `restante`.
- `plan_next_question(state, update, after_hours: bool)`:
  - nova ordem (roteiro);
  - passo agendamento (6/7 atuais) **suprimido** quando `after_hours=True` → segue direto p/ encerramento;
  - preserva: limite 2-tentativas (`_make`/`insist_attempts`), `skipped_fields`, anti-repetição.

### `orchestrator.py`
- `run_turn`: calcula `after_hours = is_after_hours()` **1x**, propaga como param.
- Fora-do-horário: **pula ramo booking** (atual `:292`); ao completar/encerrar → terminal `qualificado_fora_horario` + handoff (`_escalate`).
- Saudação condicional (texto fora-horário + vídeo), controlada por `saudacao_feita` (1x; sem re-saudar ao cruzar a borda).

### `prompts/persona.py`
- Remove proibição de repetir/echoar cidade.
- **Variante fora-do-horário**: instrução "só qualifique, não ofereça agendar, não pressione a fechar".

### `team/voice.py`
- Injeta a variante de persona quando `after_hours=True`.

### `tools/terminal.py` — `build_consolidated_note`
- **+ linha Cidade**.
- Bloco Negociação reflete os **3 eixos**: Troca (sim + modelo/ano/km/quitado), Entrada (sim + valor / não / "não definiu"), Método (funding core), Faixa de parcela.
- **Parcial incluso**: notifica sempre no encerramento; marca campos faltantes/skipped.

### `endpoints/greet.py`
- Saudação fora-do-horário + `GHLConversations.send_attachment(contact_id, AFTERHOURS_VIDEO_URL)` (`conversations.py:52`, type SMS).

---

## 6. Plano de testes (espelha padrões existentes)

- **`tests/test_hours.py`** (novo): casos borda com `now` injetado — seg 08:00 (aberto), 18:30 (fecha),
  18:31 (fora), sáb 09:00/13:00, domingo, borda 07:30.
- **`tests/test_schemas.py`** (estende): `compute_missing` na nova ordem; gates `possui_troca`/`possui_entrada`;
  `faixa_parcela` só financiamento/100; `cidade` sempre; `troca.restante` removido; `quitado` opcional.
- **`tests/test_question_planner.py`** (estende): sequência exata do roteiro
  (foco→troca→entrada→metodo→cidade); agendamento suprimido se `after_hours=True`;
  "ainda não defini" → skip; limite 2-tentativas preservado.
- **`tests/test_orchestrator.py`** (estende): fora-horário → sem booking → terminal `qualificado_fora_horario` + handoff;
  dentro → fluxo atual + cidade.
- **Nota consolidada**: cidade + 3 eixos + parcial.
- **Golden**: nova conversa fora-do-horário fim-a-fim; **atualizar goldens atuais** para incluir `cidade`.

---

## 7. Pendências / follow-ups

- **Teste de envio de vídeo** → contactId `avPTq2NVCrgqbEL5QVMz`. Rodar na **VPS** (onde `.env` real existe;
  local não tem `CRM_API_KEY`). Não colar secret no chat.
- Confirmar **nome exato da classe cliente GHL** antes do one-shot de envio.
- Comando one-shot (a validar):
  ```bash
  python -c "from ghl.client import <ClienteGHL>; from config.settings import settings; \
  <ClienteGHL>(settings).send_attachment('avPTq2NVCrgqbEL5QVMz', \
  'https://assets.cdn.filesafe.space/wbSVxrr4mvYNRaIw1eJ7/media/6a452aaab653a0ddc229a739.mp4')"
  ```

---

## 8. Fora de escopo (v1)

- Timeout de 1h sem resposta → Encerramento: tratado **no CRM**.
- Feriados: dia útil normal.
- Vídeo por upload de mídia GHL (usa URL).
