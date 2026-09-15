"""Aggregates every version-1 route.

New routers are registered here so the ``/api/v1`` prefix is applied in exactly
one place. Breaking changes create ``/api/v2`` rather than editing these routes
in place (CLAUDE.md §4).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import auth, health, vendors

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(vendors.router)
