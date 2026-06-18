"""Entrypoint.

- FastAPI app exposing /webhook/inbound + /sessions/{id}/greet + /health.
- AgentOS app for local chat + tracing/observability.

Run webhook:   uvicorn app:api --reload
Run AgentOS:   python app.py
"""
from __future__ import annotations

from fastapi import FastAPI, Response

from db.sessions import init_db
from db.usage import usage_totals
from endpoints.export import router as export_router
from endpoints.greet import router as greet_router
from endpoints.inbound import router as inbound_router
from metrics import render_metrics

api = FastAPI(title="Adrian Pre-Attendance")
api.include_router(inbound_router)
api.include_router(greet_router)
api.include_router(export_router)


@api.on_event("startup")
def _startup() -> None:
    init_db()


@api.get("/health")
def health() -> dict:
    return {"status": "ok"}


@api.get("/metrics")
def metrics() -> Response:
    return Response(content=render_metrics(), media_type="text/plain")


@api.get("/usage")
def usage() -> dict:
    """Token + BRL cost totals (per user request)."""
    return usage_totals()


@api.get("/usage/{contact_id}")
def usage_contact(contact_id: str) -> dict:
    """Token + BRL cost attributable to one contact/lead (Fase 1 / GAP-1)."""
    from db.usage import usage_by_contact

    return usage_by_contact(contact_id)


# --- AgentOS (local dev / observability) -----------------------------------
def serve_agentos() -> None:
    from agno.os import AgentOS

    from team.voice import build_voice_agent

    agent_os = AgentOS(description="Adrian", agents=[build_voice_agent()])
    app = agent_os.get_app()
    agent_os.serve(app=app)


if __name__ == "__main__":
    serve_agentos()
