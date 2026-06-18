"""DEPRECATED shim. The CRM client now lives in the modular `ghl/` package.
Kept so existing imports (`from integrations.crm import crm`) keep working while
callers migrate to `from ghl.client import crm`.
"""
from ghl.client import GhlClient as CrmClient  # noqa: F401  (back-compat alias)
from ghl.client import crm  # noqa: F401

__all__ = ["crm", "CrmClient"]
