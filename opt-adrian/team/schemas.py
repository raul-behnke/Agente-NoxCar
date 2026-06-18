"""Agno-layer schemas (pure Pydantic, no Agno import — keeps validation testable
offline). InventoryDecision = output of EstoqueExpert; BubbleSequence = output of
the voice agent (Sprint 5)."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class InventoryAction(str, Enum):
    mostrar_card_unico = "mostrar_card_unico"
    mostrar_card_lista = "mostrar_card_lista"
    comentar_em_texto = "comentar_em_texto"
    perguntar_refinamento = "perguntar_refinamento"
    nao_mostrar = "nao_mostrar"


class VeiculoSelecionado(BaseModel):
    external_id: str
    motivo_individual: Optional[str] = Field(
        default=None,
        description="Por que ESTE veículo encaixa neste turno (1 frase, ângulo de venda).",
    )


class InventoryDecision(BaseModel):
    """EstoqueExpert output. Decides WHICH vehicles to show — never talks to lead."""
    action: InventoryAction
    veiculos_selecionados: list[VeiculoSelecionado] = Field(default_factory=list)
    pergunta_refinamento: Optional[str] = None
    hint_narrativo: Optional[str] = None
    texto_sugerido_apresentacao: Optional[str] = None
    enviar_fotos_de: list[str] = Field(default_factory=list)
    motivo_geral: str = ""


class BubbleSequence(BaseModel):
    """Voice agent output (Sprint 5). abertura? + bolhas_extras + fechamento.

    The <=2 cap is enforced in compose_bubbles (not the schema) so a model that
    returns 3 still parses instead of raising — graceful degradation."""
    abertura: Optional[str] = None
    bolhas_extras: list[str] = Field(default_factory=list)
    fechamento: str
