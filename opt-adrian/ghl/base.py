"""Shared HTTP base for the GHL client. Holds the authenticated httpx client;
mixins build on `self._http`."""
from __future__ import annotations

import httpx

from config.settings import settings
from obs import log


class GhlBase:
    def __init__(self) -> None:
        headers = {"Version": "2021-07-28"}
        if settings.crm_api_key:
            headers["Authorization"] = f"Bearer {settings.crm_api_key}"
        else:
            # No key -> omit the header instead of sending an illegal "Bearer "
            # value (httpx raises LocalProtocolError on empty header). Outbound
            # calls will then fail cleanly (401) until CRM_API_KEY is set.
            log.warning("crm_api_key_unset", note="GHL calls will fail until set")
        self._http = httpx.Client(
            base_url=settings.crm_base_url, headers=headers, timeout=15.0
        )
