"""Per-turn orchestrator (reference parity: orchestrator._run_turn).

The deterministic guardrail layer that wires the 3-stage pipeline:
  Updater (extract) -> merge -> Question Planner -> [EstoqueExpert -> voice] -> send.

Hard business rules live HERE in code, not in prompts (grill decisions):
- activation tag gate (+ inativo_sem_tag); dedup; terminal/human-takeover guards
- 3 immediate escalation exceptions (explicit human / conflict / integration error)
- financiamento_100 follows the NORMAL flow (NOT an exception)
- no maturity escalation; collect -> offer scheduling -> book or escalate
- 2-attempt exhaustion -> escalate; premature scheduling allowed
- idempotent side effects; preemption per contactId

LLM-touching steps are isolated behind _extract / _generate so the control flow
is testable offline (monkeypatch those + the crm).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.merge import merge_into_state
from agent.question_planner import QuestionIntent, plan_next_question
from agent.schemas import (
    PRIORITY_FIELDS,
    Appointment,
    SessionState,
    StateUpdate,
    funnel_complete,
)
from agent.updater import run_updater
from config.settings import settings
from db.events import record_event
from db.sessions import (
    already_processed,
    bump_attempt,
    init_db,
    load_or_new,
    save,
    session_exists,
    side_effect_done,
)
from ghl.client import crm
from metrics import HANDOFF_TOTAL, QUALIFICADOS_TOTAL, TURNS_TOTAL
from obs import bind_ids, clear_ids, log
from tools.handoff import encaminhar_para_vendedor
from tools.terminal import TerminalReason, build_consolidated_note


@dataclass
class InboundEvent:
    message_id: str
    contact_id: str
    conversation_id: str
    message: str
    tags: list[str]
    lead_name: Optional[str] = None
    lead_origin: Optional[str] = None


@dataclass
class Result:
    action: str  # ignored|duplicate|replied|escalated|booked|failed
    detail: str = ""
    bubbles: list[str] = field(default_factory=list)


# --- LLM seams (monkeypatched in tests) -----------------------------------

def _extract(history: list[dict], state: SessionState, last_message: str) -> StateUpdate:
    # what we most likely asked last turn (state is still pre-update here) so the
    # updater can bind a short reply to the right field (kills the re-ask loop).
    pending = plan_next_question(state)
    return run_updater(
        history, state, last_message,
        pending_field=pending.field,
        pending_question=pending.canonical_text,
    )


async def _generate(state, next_question, update, last_message, history=None):
    from team.runner import run_team_turn  # lazy: pulls Agno only at runtime

    return await run_team_turn(state, next_question, update, last_message, history=history)


# --- helpers --------------------------------------------------------------

def _human_took_over(history: list[dict]) -> bool:
    """Human takeover is governed by the activation TAG, not message heuristics:
    GHL marks both bot and manual sends as source='app', so message inspection is
    unreliable. When a consultant takes over, the operator removes the `agente-ia`
    tag and the gate (step 1) stops Adrian. Kept as a hook; always False here."""
    return False


def _send(contact_id: str, bubbles: list[str], photos: list[str] | None = None) -> None:
    # photos first, then bubbles (reference order)
    for url in photos or []:
        try:
            crm.send_attachment(contact_id, url)
        except Exception:  # noqa: BLE001 - photo send best-effort
            log.warning("photo_send_failed", url=url)
    for b in bubbles:
        crm.send_message(contact_id, b)


def _escalate(state: SessionState, ev: InboundEvent, reason: TerminalReason, motivo: str) -> Result:
    HANDOFF_TOTAL.labels(reason.value).inc()
    if reason == TerminalReason.qualificado_sem_agenda:
        QUALIFICADOS_TOTAL.labels(reason.value).inc()
    note = build_consolidated_note(state, reason.value, motivo)
    try:
        res = encaminhar_para_vendedor(
            crm, ev.contact_id, ev.conversation_id, note, remove_tag_value=settings.activation_tag
        )
    except Exception as exc:  # noqa: BLE001 - CRM failure must not crash the turn
        log.warning("handoff_failed", contact_id=ev.contact_id, error=str(exc))
        res = {"escalated": False, "error": str(exc)}
    state.terminal_reason = reason.value
    state.stage = "escalonado"
    # commercial: sync an opportunity for qualified outcomes (Fase 3 / GAP-5)
    if reason == TerminalReason.qualificado_sem_agenda:
        _sync_opportunity(state, ev, reason.value)
    save(state)
    record_event("HANDOFF_CREATED", ev.contact_id, ev.conversation_id,
                 {"reason": reason.value, "motivo": motivo})
    record_event("CONVERSATION_COMPLETED", ev.contact_id, ev.conversation_id,
                 {"terminal_reason": reason.value})
    if res.get("duplicate"):
        return Result("duplicate", "escalonamento já realizado")
    # transition message to the lead (PRD §6.6) — contextual ao motivo.
    if reason == TerminalReason.handoff_solicitado:
        msg = "Claro! Já vou te conectar com um consultor que segue com você daqui. 👍"
    else:
        msg = (
            "Perfeito! Já deixei todas as informações registradas. "
            "Nosso vendedor entrará em contato no horário comercial para continuar seu atendimento."
        )
    try:
        crm.send_message(ev.contact_id, msg)
    except Exception:  # noqa: BLE001
        pass
    return Result("escalated", motivo)


def _safe_escalate(state: SessionState, ev: InboundEvent, motivo: str) -> Result:
    """Integration-failure fallback (PRD §12.8) -> handoff_erro."""
    log.warning("safe_escalate", contact_id=ev.contact_id, motivo=motivo)
    return _escalate(state, ev, TerminalReason.handoff_erro, motivo)


def _parse_slot(iso: str):
    """Parse an ISO slot to an aware datetime (naive -> app timezone)."""
    from dateutil import parser as dtparser
    from zoneinfo import ZoneInfo

    dt = dtparser.isoparse(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(settings.app_timezone))
    return dt


def _slot_matches(chosen_iso: str, free: list[str]) -> bool:
    """True if `chosen_iso` equals one of the calendar's free slots (same instant)."""
    try:
        target = _parse_slot(chosen_iso)
    except Exception:  # noqa: BLE001 - unparseable -> treat as unavailable
        return False
    for s in free:
        try:
            if _parse_slot(s) == target:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _format_slot_offer(free: list[str], n: int = 3) -> list[str]:
    """Human-readable list of the next available slots (re-offer message)."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.app_timezone)
    lines = []
    for s in free[:n]:
        try:
            lines.append("• " + _parse_slot(s).astimezone(tz).strftime("%d/%m %H:%M"))
        except Exception:  # noqa: BLE001
            continue
    opcoes = "\n".join(lines) if lines else "(consulte horários com um consultor)"
    return [
        "Esse horário não está disponível. Tenho estes:",
        opcoes,
        "Qual fica melhor pra você?",
    ]


def _sync_opportunity(state: SessionState, ev: InboundEvent, outcome: str) -> None:
    """Create/update a GHL opportunity for a qualified lead (Fase 3 / GAP-5).

    Best-effort and gated on pipeline config: never crash a turn over commercial
    sync. Stores the opportunity_id back on the state so it travels with the
    snapshot and isn't recreated."""
    if not settings.crm_pipeline_id or not settings.crm_pipeline_stage_id:
        return  # commercial sync not configured -> skip silently
    name = state.collected.nome or ev.lead_name or ev.contact_id
    title = f"{name} - {state.collected.veiculo_interesse or 'Veículo'}"
    try:
        if state.opportunity_id:
            crm.update_opportunity(state.opportunity_id, status="open", name=title)
            opp_id = state.opportunity_id
        else:
            res = crm.create_opportunity(ev.contact_id, title)
            opp_id = (res or {}).get("opportunity", {}).get("id") or (res or {}).get("id")
            state.opportunity_id = opp_id
        record_event("OPPORTUNITY_CREATED", ev.contact_id, ev.conversation_id,
                     {"opportunity_id": opp_id,
                      "pipeline_id": settings.crm_pipeline_id,
                      "stage_id": settings.crm_pipeline_stage_id,
                      "outcome": outcome, "title": title})
    except Exception as exc:  # noqa: BLE001 - commercial sync must not break the turn
        log.warning("opportunity_sync_failed", contact_id=ev.contact_id, error=str(exc))


# --- core pipeline --------------------------------------------------------

async def run_turn(ev: InboundEvent) -> Result:
    init_db()

    # 1. activation tag gate (PRD §4.1/§4.2)
    if settings.activation_tag not in ev.tags:
        state = load_or_new(ev.contact_id, ev.conversation_id)
        if not state.terminal_reason:
            state.terminal_reason = TerminalReason.inativo_sem_tag.value
            save(state)
        return Result("ignored", "sem tag de ativação")

    # 2. dedup (kept alongside preemption — Q7)
    if already_processed(ev.message_id):
        return Result("duplicate")

    # 3-5. state + terminal guard
    is_new = not session_exists(ev.contact_id)
    state = load_or_new(ev.contact_id, ev.conversation_id)
    log.info("turn_start", contact_id=ev.contact_id, message_id=ev.message_id,
             msg=(ev.message or "")[:80])
    if state.terminal_reason:
        return Result("ignored", f"sessão encerrada ({state.terminal_reason})")

    if is_new:
        record_event(
            "CONVERSATION_STARTED", ev.contact_id, ev.conversation_id,
            {"lead_name": ev.lead_name, "lead_origin": ev.lead_origin},
        )
    # follow-up reply: the lead came back after a re-engagement nudge (Fase 3)
    if state.followup_pending:
        state.followup_pending = False
        record_event("FOLLOWUP_FINISHED", ev.contact_id, ev.conversation_id,
                     {"followup_count": state.followup_count})

    # 6. history = source of truth; failure -> handoff_erro
    try:
        history = await asyncio.wait_for(
            asyncio.to_thread(crm.get_history, ev.contact_id),
            timeout=settings.crm_timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001 (inclui TimeoutError)
        return _safe_escalate(state, ev, f"falha ao ler histórico: {exc}")
    log.info("history_loaded", contact_id=ev.contact_id, turns=len(history))

    # continuation? if the store already replied before, don't re-greet
    if not state.saudacao_feita and any(
        m.get("direction") == "outbound" for m in history
    ):
        state.saudacao_feita = True

    # 7. human takeover (PRD §11.5)
    if _human_took_over(history):
        return Result("ignored", "atendimento humano em curso")

    # 8. extract state. The Updater uses the SYNCHRONOUS OpenAI client; running it
    # inline would block the asyncio event loop for the whole (slow, reasoning)
    # call — starving the Uvicorn worker heartbeat until gunicorn SIGABRTs it
    # (WORKER TIMEOUT) and the reply is never sent. Offload to a thread so the
    # loop stays responsive and preemption cancel works at await points.
    try:
        update = await asyncio.wait_for(
            asyncio.to_thread(_extract, history, state, ev.message),
            timeout=settings.llm_timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001 (inclui TimeoutError)
        return _safe_escalate(state, ev, f"falha na extração: {exc}")

    # 9. immediate escalation exceptions (grill Q4 — only 3)
    if update.pediu_humano:
        state = merge_into_state(state, update)
        return _escalate(state, ev, TerminalReason.handoff_solicitado, "pedido explícito de humano")
    if update.conflito:
        state = merge_into_state(state, update)
        return _escalate(state, ev, TerminalReason.handoff_solicitado, "conflito/emocional")

    # 10. merge
    state = merge_into_state(state, update)

    # 11. booking: lead chose a slot (premature allowed — Q4). Validate the slot
    # against the calendar BEFORE booking — never blind-book an LLM-parsed time
    # ("amanhã 17h") that may not exist / be free.
    if update.chosen_slot_iso:
        try:
            free = crm.get_free_slots()
        except Exception as exc:  # noqa: BLE001 - can't verify -> integration fallback
            return _safe_escalate(state, ev, f"falha ao ler agenda: {exc}")
        if not free:
            return _escalate(
                state, ev, TerminalReason.qualificado_sem_agenda,
                "sem horários disponíveis para agendamento automático",
            )
        if not _slot_matches(update.chosen_slot_iso, free):
            bubbles = _format_slot_offer(free)
            try:
                _send(ev.contact_id, bubbles)
            except Exception as exc:  # noqa: BLE001
                return _safe_escalate(state, ev, f"falha ao enviar: {exc}")
            state.saudacao_feita = True
            save(state)
            return Result("replied", "agendamento_indisponivel", bubbles=bubbles)
        if not side_effect_done(ev.conversation_id, "booking"):
            try:
                crm.create_appointment(
                    ev.contact_id, update.chosen_slot_iso, f"Visita - {state.collected.veiculo_interesse or ''}"
                )
            except Exception as exc:  # noqa: BLE001
                return _safe_escalate(state, ev, f"falha ao agendar: {exc}")
        state.appointment = Appointment(slot_iso=update.chosen_slot_iso, created=True)
        state.terminal_reason = TerminalReason.qualificado_agendado.value
        state.stage = "agendamento_criado"
        _sync_opportunity(state, ev, state.terminal_reason)
        try:
            crm.add_note(ev.contact_id, build_consolidated_note(state, state.terminal_reason))
            crm.send_message(ev.contact_id, "Agendamento confirmado! Te espero. 👍")
        except Exception:  # noqa: BLE001
            pass
        save(state)
        record_event("APPOINTMENT_CREATED", ev.contact_id, ev.conversation_id,
                     {"slot_iso": update.chosen_slot_iso,
                      "veiculo": state.collected.veiculo_interesse})
        record_event("CONVERSATION_COMPLETED", ev.contact_id, ev.conversation_id,
                     {"terminal_reason": state.terminal_reason})
        QUALIFICADOS_TOTAL.labels(TerminalReason.qualificado_agendado.value).inc()
        return Result("booked", update.chosen_slot_iso)

    # 12. plan the next question
    nq = plan_next_question(state, update)

    # 13. 2-attempt exhaustion: STOP insisting and move on (do NOT escalate — the
    # lead is engaged, just not answering this field, e.g. won't give the name).
    # Skipping keeps the conversation alive instead of a premature handoff.
    guard = 0
    while nq.exhausted and nq.field and guard < len(PRIORITY_FIELDS) + 5:
        guard += 1
        if nq.field not in state.skipped_fields:
            state.skipped_fields.append(nq.field)
            log.info("field_skipped_after_attempts", contact_id=ev.contact_id, field=nq.field)
        nq = plan_next_question(state, update)

    # 14. funil completo -> handoff DIRETO (sem oferecer agendamento). O vendedor
    #     entra em contato no horário comercial pra dar sequência.
    if funnel_complete(state.collected):
        return _escalate(
            state, ev, TerminalReason.qualificado_sem_agenda, "qualificado, handoff para o vendedor"
        )

    # 15. count this re-ask (anti-insistence). Conta SEMPRE que um campo do funil
    # é perguntado — inclusive quando o turno é `duvida` carregando a pergunta do
    # funil (multi-intenção). Sem isso o campo nunca esgota e repete infinito.
    if nq.field and nq.intent in (QuestionIntent.funil, QuestionIntent.foco, QuestionIntent.duvida):
        bump_attempt(state, nq.field)

    # 16. generate the turn (EstoqueExpert -> voice)
    try:
        turn = await _generate(state, nq, update, ev.message, history=history)
    except Exception as exc:  # noqa: BLE001
        return _safe_escalate(state, ev, f"falha na geração: {exc}")

    # 16b. envia o vídeo da estrutura 1x, junto da saudação (sempre no 1º contato).
    if not state.saudacao_feita and settings.greeting_video_url:
        try:
            crm.send_attachment(ev.contact_id, settings.greeting_video_url)
        except Exception:  # noqa: BLE001 - vídeo best-effort, não trava o turno
            log.warning("greeting_video_failed", contact_id=ev.contact_id)

    # update offer-tracking state
    if turn.shown_external_ids:
        state.vehicles_shown = list(dict.fromkeys(state.vehicles_shown + turn.shown_external_ids))
        state.last_card_external_id = turn.shown_external_ids[-1]

    # send photos + bubbles
    try:
        _send(ev.contact_id, turn.bubbles, getattr(turn, "photos", None))
    except Exception as exc:  # noqa: BLE001
        return _safe_escalate(state, ev, f"falha ao enviar: {exc}")

    state.saudacao_feita = True  # greeting now done; continuation turns won't re-greet
    save(state)
    return Result("replied", nq.intent.value, bubbles=turn.bubbles)


# --- preemption per contactId (Q7) ----------------------------------------

_TASKS: dict[str, asyncio.Task] = {}

# Fire-and-forget background tasks (webhook must return fast; GHL times out at 60s
# while the 3-LLM pipeline runs). We keep strong refs so they aren't GC'd.
_BG_TASKS: set[asyncio.Task] = set()


def run_in_background(coro) -> asyncio.Task:
    """Schedule a coroutine to run detached; the webhook returns immediately and
    the reply reaches the lead via the CRM API when the pipeline finishes."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def process_turn(ev: InboundEvent) -> Result:
    """Cancel any in-flight turn for the same contact, then run this one. The send
    phase inside run_turn should be treated as non-preemptable by callers."""
    prev = _TASKS.get(ev.contact_id)
    if prev and not prev.done():
        prev.cancel()
    # attribute every cost row + log in this turn to the conversation (Fase 1/GAP-1)
    bind_ids(ev.contact_id, ev.conversation_id)
    task = asyncio.ensure_future(run_turn(ev))
    _TASKS[ev.contact_id] = task
    try:
        # backstop: um turno que trave (CRM/LLM sem resposta) não pode ficar
        # pendurado em silêncio — estoura timeout e vira erro visível.
        result = await asyncio.wait_for(asyncio.shield(task), timeout=settings.turn_timeout_sec)
        TURNS_TOTAL.labels(result.action).inc()
        return result
    except asyncio.TimeoutError:
        log.error("turn_timeout", contact_id=ev.contact_id, message_id=ev.message_id)
        task.cancel()
        return Result("failed", "turno excedeu o tempo limite")
    except asyncio.CancelledError:
        # a newer message for this contact preempted this turn — expected
        log.info("turn_preempted", contact_id=ev.contact_id, message_id=ev.message_id)
        return Result("preempted", "turno substituído por mensagem mais recente")
    except Exception as exc:  # noqa: BLE001 - never surface a 500 to the CRM
        log.error("turn_unhandled_error", contact_id=ev.contact_id, error=str(exc))
        return Result("failed", str(exc)[:200])
    finally:
        if _TASKS.get(ev.contact_id) is task:
            _TASKS.pop(ev.contact_id, None)
        clear_ids()
