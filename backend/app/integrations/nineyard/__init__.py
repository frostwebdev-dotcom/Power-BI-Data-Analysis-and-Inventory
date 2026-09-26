"""Nineyard integration.

The client and catalog reader are read-only.  The probe remains the diagnostic
entry point; the reader implements the pagination and envelopes documented in
Nineyard's published OpenAPI contract for the catalog synchronization service.

Nothing in this package can mutate anything in Nineyard: the client exposes one
read method and no HTTP verb parameter.
"""

from app.integrations.nineyard.catalog import NineyardCatalogReader, SkuProductMapping
from app.integrations.nineyard.client import (
    AUTH_PATH,
    NineyardClient,
    NineyardConfig,
    TokenInfo,
)
from app.integrations.nineyard.probe import (
    READ_ONLY_ENDPOINTS,
    EndpointReport,
    ProbeReport,
    probe_endpoint,
    run_probe,
)

__all__ = [
    "AUTH_PATH",
    "READ_ONLY_ENDPOINTS",
    "EndpointReport",
    "NineyardCatalogReader",
    "NineyardClient",
    "NineyardConfig",
    "ProbeReport",
    "SkuProductMapping",
    "TokenInfo",
    "probe_endpoint",
    "run_probe",
]
