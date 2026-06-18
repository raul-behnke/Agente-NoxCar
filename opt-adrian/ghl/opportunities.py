"""Opportunities API (Fase 3 / GAP-5): the commercial layer Adrian lacked.

Creates/updates a GHL opportunity in the configured pipeline when a lead is
qualified, so the ZOI Performance Hub can attribute pipeline movement (and,
later, sales) to the AI. GHL v2 endpoints:
  POST /opportunities/         create
  PUT  /opportunities/{id}     update
"""
from __future__ import annotations

from typing import Any, Optional

from config.settings import settings


class OpportunitiesMixin:
    def create_opportunity(
        self,
        contact_id: str,
        name: str,
        monetary_value: Optional[float] = None,
        status: str = "open",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pipelineId": settings.crm_pipeline_id,
            "pipelineStageId": settings.crm_pipeline_stage_id,
            "locationId": settings.crm_location_id,
            "contactId": contact_id,
            "name": name,
            "status": status,
        }
        if monetary_value is not None:
            payload["monetaryValue"] = monetary_value
        r = self._http.post("/opportunities/", json=payload)
        r.raise_for_status()
        return r.json()

    def update_opportunity(
        self,
        opportunity_id: str,
        status: Optional[str] = None,
        name: Optional[str] = None,
        pipeline_stage_id: Optional[str] = None,
        monetary_value: Optional[float] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if status is not None:
            payload["status"] = status
        if name is not None:
            payload["name"] = name
        if pipeline_stage_id is not None:
            payload["pipelineStageId"] = pipeline_stage_id
        if monetary_value is not None:
            payload["monetaryValue"] = monetary_value
        r = self._http.put(f"/opportunities/{opportunity_id}", json=payload)
        r.raise_for_status()
        return r.json()
