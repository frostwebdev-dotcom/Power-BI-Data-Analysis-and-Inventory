"""Aggregates every version-1 route.

New routers are registered here so the ``/api/v1`` prefix is applied in exactly
one place. Breaking changes create ``/api/v2`` rather than editing these routes
in place (CLAUDE.md §4).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import (
    amazon,
    audit,
    auth,
    dashboard,
    exceptions,
    financial_imports,
    health,
    import_profiles,
    imports,
    products,
    purchasing_policies,
    recommendations,
    replenishment_now,
    replenishment_review,
    vendor_mail,
    vendors,
    watchlist,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(amazon.router)
api_router.include_router(vendors.router)
api_router.include_router(import_profiles.router)
api_router.include_router(import_profiles.schemas_router)
api_router.include_router(imports.router)
api_router.include_router(financial_imports.router)
api_router.include_router(products.router)
api_router.include_router(purchasing_policies.router)
api_router.include_router(recommendations.router)
api_router.include_router(replenishment_review.router)
api_router.include_router(replenishment_now.router)
api_router.include_router(exceptions.router)
api_router.include_router(watchlist.router)
api_router.include_router(watchlist.availability_router)
api_router.include_router(audit.router)
api_router.include_router(dashboard.router)
api_router.include_router(vendor_mail.router)
