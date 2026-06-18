"""Webhook authentication (reference parity: security.py).

The CRM calls the inbound webhook with a shared secret (e.g. ?secret=...).
We compare it with `hmac.compare_digest` to avoid timing attacks. If no secret
is configured, the check is disabled (dev) and logs a warning.
"""
from __future__ import annotations

import hmac

from config.settings import settings
from obs import log


def require_secret(provided: str | None) -> bool:
    """Return True if the provided secret matches the configured webhook secret."""
    expected = getattr(settings, "webhook_secret", "") or ""
    if not expected:
        log.warning("webhook_secret_unset", note="webhook auth disabled (dev)")
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def require_secret_strict(provided: str | None) -> bool:
    """Strict variant for data-export endpoints (v2 / GAP-11). Unlike the webhook
    check, this REFUSES when no secret is configured — the export must never be
    open. Returns False if the secret is unset or mismatched."""
    expected = getattr(settings, "webhook_secret", "") or ""
    if not expected:
        log.warning("export_blocked_secret_unset", note="set ADRIAN_WEBHOOK_SECRET")
        return False
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def require_export_secret(provided: str | None, since: int) -> bool:
    import hashlib
    expected = getattr(settings, "export_secret", "") or ""
    if not expected or not provided:
        if not expected:
            log.warning("export_blocked_secret_unset", note="set ADRIAN_EXPORT_SECRET")
        return False
    h = hmac.new(expected.encode(), str(since).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, h) or hmac.compare_digest(provided, expected)
