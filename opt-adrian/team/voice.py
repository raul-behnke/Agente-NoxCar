"""Adrian voice agent — Agno Agent that writes the bubbles to the lead.

Standalone Agent (NOT a Team leader — reference abandoned coordinate due to
leader hallucination). output_schema=BubbleSequence. Imports Agno; pure
composition lives in team/rendering.py.
"""
from __future__ import annotations

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools import tool

from config.settings import settings
from prompts.persona import VOICE_INSTRUCTIONS
from team.schemas import BubbleSequence
from tools.faq import get_faq_raw


@tool()
def consultar_faq() -> str:
    """Retorna o FAQ da loja (YAML). Responda dúvidas SOMENTE a partir deste conteúdo."""
    return get_faq_raw()


def build_voice_agent() -> Agent:
    return Agent(
        name="Adrian",
        model=OpenAIChat(id=settings.model_id, reasoning_effort=settings.reasoning_effort),
        instructions=VOICE_INSTRUCTIONS,
        output_schema=BubbleSequence,
        tools=[consultar_faq],
        markdown=False,
        telemetry=False,
    )
