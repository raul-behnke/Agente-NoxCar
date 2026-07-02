"""run_team_turn — sequential orchestration of EstoqueExpert -> voice (Adrian).

Reference parity (team/runner.py): NO Agno Team. A deterministic signal decides
whether to call the EstoqueExpert; its validated decision + rendered cards are
injected into the voice agent's payload. Imports Agno; deterministic composition
is delegated to team/rendering.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent.schemas import SessionState, StateUpdate
from team.inventory_expert import call_inventory_expert
from team.rendering import (
    ai_identity_directive,
    build_voice_payload,
    compose_bubbles,
    render_cards_from_decision,
)
from config.settings import settings
from team.schemas import InventoryAction, InventoryDecision
from team.validation import (
    constrain_offer,
    detect_inventory_signal,
    lead_wants_options,
    should_send_photos,
)
from team.voice import build_voice_agent
from tools.faq import get_faq_raw
from tools.inventory import load_inventory
from tools.photos import build_photo_payload_by_id, resolve_photos


@dataclass
class TurnResult:
    bubbles: list[str]
    photos: list[str] = field(default_factory=list)
    decision: Optional[InventoryDecision] = None
    shown_external_ids: list[str] = field(default_factory=list)


_PHOTO_WORDS = ("foto", "fotos", "imagem", "imagens", "fotinha")


def _lead_pediu_foto(update, last_message: str) -> bool:
    """Lead pediu foto? (intent/topics pedido_foto ou palavra 'foto' na mensagem)"""
    if update is not None:
        if getattr(update, "intent", None) == "pedido_foto" or \
                getattr(update, "intent_secundario", None) == "pedido_foto" or \
                "pedido_foto" in (getattr(update, "topics", None) or []):
            return True
    return bool(last_message) and any(w in last_message.lower() for w in _PHOTO_WORDS)


def _recent_history(history: Optional[list[dict]], limit: int = 6) -> str:
    """Compact last turns so the EstoqueExpert sees refusals ('nenhum me agradou')."""
    if not history:
        return "(sem histórico)"
    lines = []
    for m in history[-limit:]:
        who = "LEAD" if m.get("direction") == "inbound" else "LOJA"
        body = (m.get("body") or m.get("message") or "").strip()
        if body:
            lines.append(f"{who}: {body}")
    return "\n".join(lines) or "(sem histórico)"


async def run_team_turn(
    state: SessionState,
    next_question,
    update: Optional[StateUpdate],
    last_message: str,
    inventory: Optional[list[dict]] = None,
    history: Optional[list[dict]] = None,
) -> TurnResult:
    inv = inventory if inventory is not None else load_inventory()
    by_id = {str(v.get("external_id")): v for v in inv}

    allow_opts = lead_wants_options(update, last_message)

    # describe already-shown vehicles so the expert can RESOLVE references like
    # "fotos dessa tracker" / "me fala do kicks" to the exact vehicle.
    shown_desc = []
    for vid in state.vehicles_shown:
        v = by_id.get(str(vid))
        if v:
            d = " ".join(str(x) for x in (v.get("brand"), v.get("model"), v.get("version"), v.get("year")) if x)
            shown_desc.append(f"{vid}: {d}")

    # 1. deterministic gate: do we need the EstoqueExpert this turn?
    decision: Optional[InventoryDecision] = None
    if detect_inventory_signal(state, update, last_message):
        payload = (
            f"Veículo de interesse do lead: {state.collected.veiculo_interesse}\n"
            f"Mensagem do lead (turno atual): {last_message}\n"
            f"HISTÓRICO RECENTE da conversa:\n{_recent_history(history)}\n\n"
            f"Veículos JÁ MOSTRADOS (existem no estoque):\n" + ("\n".join(shown_desc) or "(nenhum)") + "\n"
            f"O lead pediu OUTRAS opções? {'SIM' if allow_opts else 'não'}\n"
            "Se o lead perguntou sobre UM veículo já mostrado (ex.: 'fotos dessa Tracker', "
            "'detalhes do Kicks'), retorne ESSE veículo (mostrar_card_unico) — ele EXISTE, "
            "está nos JÁ MOSTRADOS; use o external_id correspondente.\n"
            "Se pediu OUTRAS opções (SIM), aí sim NÃO repita os JÁ MOSTRADOS — ofereça DIFERENTES "
            "do mesmo segmento/faixa (mostrar_card_lista).\n"
            "Preencha motivo_individual de cada veículo (ângulo de venda) e hint_narrativo."
        )
        decision = await call_inventory_expert(payload, inv)

    # anti-repetition (deterministic): when the lead asks for OTHER options, never
    # re-offer an already-shown vehicle — don't rely on the LLM obeying.
    if decision and allow_opts and state.vehicles_shown:
        shown = set(state.vehicles_shown)
        decision.veiculos_selecionados = [
            v for v in decision.veiculos_selecionados if v.external_id not in shown
        ]
        decision.enviar_fotos_de = [f for f in decision.enviar_fotos_de if f not in shown]

    # 1b. business rule: no auto-lists / no bullet cards. Collapse to the single
    # closest vehicle (prose) unless the lead explicitly asked for options.
    decision = constrain_offer(decision, allow_opts)

    # resolve selected vehicles -> highlighted single + (if asked) up to 3 options.
    # carry motivo_individual (sales angle) onto each vehicle dict for the voice.
    selected = []
    if decision:
        for s in decision.veiculos_selecionados:
            v = by_id.get(s.external_id)
            if v:
                vv = dict(v)
                vv["motivo"] = s.motivo_individual
                selected.append(vv)
    veiculo_destaque = selected[0] if selected else None
    veiculos_opcoes = selected[:3] if allow_opts else []

    # anti-dupla-ficha: se o veículo em destaque JÁ foi mostrado e o lead não pediu
    # a FICHA explicitamente (só perguntou foto/atributo), não re-apresenta a ficha.
    # A voice ainda responde via veiculo_em_foco (atributos) e o contrato de fotos.
    _ficha_pedida = any(
        w in (last_message or "").lower()
        for w in ("ficha", "detalhe", "especific", "informaç", "ficha completa")
    )
    if (
        veiculo_destaque
        and not allow_opts
        and not _ficha_pedida
        and str(veiculo_destaque.get("external_id")) in {str(v) for v in state.vehicles_shown}
    ):
        veiculo_destaque = None

    # 2. no bullet cards; resolve photo URLs (so the voice knows if photos exist)
    cards: list[str] = []
    shown = [s.external_id for s in decision.veiculos_selecionados] if decision else []
    # Photos only for a single focused vehicle OR explicit lead request (not on
    # a multi-option list before the lead picks one).
    photos = []
    fotos_indisponiveis = False
    # o lead quer fotos? (pedido explícito OU card único apresentado)
    quer_fotos = should_send_photos(decision, update, last_message) or (
        _lead_pediu_foto(update, last_message)
    )
    if quer_fotos:
        # alvo: ids válidos do EstoqueExpert; se vierem vazios/errados, cai pro
        # veículo EM FOCO (last_card / último mostrado). Evita "sem fotos" por
        # id hallucinado quando o carro em foco TEM fotos.
        foto_ids = [
            e for e in (decision.enviar_fotos_de if decision else [])
            if build_photo_payload_by_id(e, inv)
        ]
        if not foto_ids:
            foco_id = state.last_card_external_id or (
                state.vehicles_shown[-1] if state.vehicles_shown else None
            )
            if foco_id and build_photo_payload_by_id(str(foco_id), inv):
                foto_ids = [str(foco_id)]
        disponiveis = max(
            (len(build_photo_payload_by_id(eid, inv)) for eid in foto_ids), default=0
        )
        if disponiveis < settings.min_photos_to_send:
            fotos_indisponiveis = True  # não envia; consultor manda depois
        else:
            per = (
                settings.photos_per_vehicle_single
                if len(selected) <= 1
                else settings.photos_per_vehicle_list
            )
            photos = resolve_photos(
                foto_ids, inv, per_vehicle=per, total=settings.photos_total_max
            )

    # veículo EM FOCO (paridade AMC): ficha completa do último veículo mostrado,
    # SEMPRE disponível — mesmo sem o EstoqueExpert rodar neste turno. Deixa a voice
    # responder atributos ("qual a cor?") sem re-exibir a ficha nem alucinar.
    veiculo_em_foco = veiculo_destaque
    if veiculo_em_foco is None:
        foco_id = state.last_card_external_id or (
            state.vehicles_shown[-1] if state.vehicles_shown else None
        )
        if foco_id:
            veiculo_em_foco = by_id.get(str(foco_id))

    # 3. build voice payload + run voice agent
    voice = build_voice_agent()
    payload = build_voice_payload(
        state=state,
        next_question=next_question,
        decision=decision,
        cards=cards,
        faq_yaml=get_faq_raw(),
        ai_directive=ai_identity_directive(state.ai_identity_asked_count),
        last_message=last_message,
        photos=photos,
        veiculo_destaque=veiculo_destaque,
        veiculos_opcoes=veiculos_opcoes,
        veiculo_em_foco=veiculo_em_foco,
        fotos_indisponiveis=fotos_indisponiveis,
    )
    result = await voice.arun(input=payload)
    seq = result.content
    from team.usage import record_agno_usage

    record_agno_usage("voice", result)

    # 4. compose final bubbles (photos already resolved above)
    bubbles = compose_bubbles(seq, cards)
    return TurnResult(bubbles=bubbles, photos=photos, decision=decision, shown_external_ids=shown)
