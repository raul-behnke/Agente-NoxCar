"""Smoke-test a running Adrian server (local or via ngrok) WITHOUT spending LLM
quota or hitting the real CRM.

Usage:
    python -m scripts.smoke_webhook [BASE_URL] [SECRET]
    # e.g. python -m scripts.smoke_webhook http://localhost:8000
    #      python -m scripts.smoke_webhook https://abc.ngrok.io mysecret

Checks /health, /metrics, /usage and a no-tag /webhook/inbound (which short-
circuits to "ignored" before any LLM/CRM call). To exercise a real turn, send a
WhatsApp message to a contact tagged `agente-ia` from the actual CRM.
"""
from __future__ import annotations

import sys

import httpx


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    secret = sys.argv[2] if len(sys.argv) > 2 else ""
    base = base.rstrip("/")
    ok = True

    with httpx.Client(base_url=base, timeout=15.0) as c:
        h = c.get("/health")
        print("GET /health ->", h.status_code, h.json())
        ok &= h.status_code == 200

        m = c.get("/metrics")
        has = b"adrian_turns_total" in m.content
        print("GET /metrics ->", m.status_code, "exposition:", has)
        ok &= m.status_code == 200 and has

        u = c.get("/usage")
        print("GET /usage ->", u.status_code, u.json())
        ok &= u.status_code == 200

        url = "/webhook/inbound" + (f"?secret={secret}" if secret else "")
        r = c.post(url, json={
            "messageId": "smoke-1", "contactId": "smoke-c", "conversationId": "smoke-conv",
            "tags": [], "message": "ping",
        })
        body = r.json()
        print("POST /webhook/inbound (no tag) ->", r.status_code, body)
        ok &= r.status_code == 200 and body.get("action") == "ignored"

    print("\nSMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
