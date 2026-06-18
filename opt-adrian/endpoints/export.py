"""Incremental export para o ZOI Performance Hub (secret dedicado + shape canônico)."""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from db.events import events_since
from db.usage import usage_since
from security import require_export_secret

router = APIRouter()
_MAX = 5000

def _clamp(n): return max(1, min(int(n), _MAX))
def _unauth(): return JSONResponse(status_code=401, content={"action": "unauthorized"})

@router.get("/export/events")
def export_events(request: Request, since: int = 0, limit: int = 1000):
    if not require_export_secret(request.query_params.get("secret"), since): return _unauth()
    rows = events_since(since, _clamp(limit))
    return {"events": rows, "next_cursor": rows[-1]["cursor_id"] if rows else None, "count": len(rows)}

@router.get("/export/usage")
def export_usage(request: Request, since: int = 0, limit: int = 1000):
    if not require_export_secret(request.query_params.get("secret"), since): return _unauth()
    rows = usage_since(since, _clamp(limit))
    return {"rows": rows, "next_cursor": rows[-1]["cursor_id"] if rows else None, "count": len(rows)}
