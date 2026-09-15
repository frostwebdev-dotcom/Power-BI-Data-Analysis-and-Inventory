"""API-shape rules that must hold for every route ever added (AC-0.6)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

# The only paths permitted outside a version prefix. Container and orchestrator
# probes need a stable path that survives API versioning (CLAUDE.md §4).
UNVERSIONED_ALLOWLIST = {"/health"}

VERSION_PREFIX = "/api/v1"


def _documented_paths(app: FastAPI) -> set[str]:
    """Every path in the OpenAPI schema.

    The schema is the public contract and is stable across FastAPI versions.
    Walking ``app.routes`` is not: since 0.141 an included router is nested
    behind a private ``_IncludedRouter`` that exposes no ``routes`` attribute,
    so a naive walk silently finds nothing and the assertions below pass
    vacuously.
    """
    return set(app.openapi()["paths"].keys())


def test_every_route_is_versioned_or_explicitly_allowlisted(app: FastAPI) -> None:
    paths = _documented_paths(app)
    assert paths, "no documented paths — the enumeration is broken, not the app"

    offenders = {
        path
        for path in paths
        if not path.startswith(VERSION_PREFIX) and path not in UNVERSIONED_ALLOWLIST
    }

    assert offenders == set(), f"routes outside {VERSION_PREFIX}: {sorted(offenders)}"


def test_no_infrastructure_url_escapes_the_version_prefix(app: FastAPI) -> None:
    """Docs, schema, and the OAuth2 redirect must all be versioned.

    ``swagger_ui_oauth2_redirect_url`` does not follow ``docs_url``; left at its
    default it mounts an unversioned ``/docs/oauth2-redirect`` route.
    """
    infrastructure_urls = [
        app.openapi_url,
        app.docs_url,
        app.redoc_url,
        app.swagger_ui_oauth2_redirect_url,
    ]

    for url in infrastructure_urls:
        assert url is not None
        assert url.startswith(VERSION_PREFIX), f"{url} is not under {VERSION_PREFIX}"


def test_openapi_schema_is_served(client: TestClient) -> None:
    response = client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert set(response.json()["paths"]) == {
        "/health",
        "/api/v1/health",
        "/api/v1/auth/me",
        "/api/v1/auth/dev-token",
        "/api/v1/vendors",
        "/api/v1/vendors/{vendor_id}",
        "/api/v1/vendors/{vendor_id}/deactivate",
        "/api/v1/vendors/{vendor_id}/contacts",
        "/api/v1/vendors/{vendor_id}/contacts/{contact_id}",
        "/api/v1/vendors/{vendor_id}/contacts/{contact_id}/deactivate",
        "/api/v1/vendors/{vendor_id}/import-profiles",
        "/api/v1/vendors/{vendor_id}/import-profiles/validate",
        "/api/v1/vendors/{vendor_id}/import-profiles/{profile_id}",
        "/api/v1/vendors/{vendor_id}/import-profiles/{profile_id}/deactivate",
        "/api/v1/vendors/{vendor_id}/import-profiles/{profile_id}/validate",
        "/api/v1/import-profiles/rule-schemas",
        "/api/v1/imports",
        "/api/v1/imports/{job_id}",
        "/api/v1/imports/{job_id}/raw",
    }


def test_every_vendor_route_documents_403(client: TestClient) -> None:
    """The OpenAPI document must tell a client every vendor route needs a role."""
    paths = client.get("/api/v1/openapi.json").json()["paths"]

    for path, operations in paths.items():
        if not path.startswith(("/api/v1/vendors", "/api/v1/import-profiles", "/api/v1/imports")):
            continue
        for method, operation in operations.items():
            assert "403" in operation["responses"], f"{method.upper()} {path} lacks 403"


def test_both_required_health_endpoints_exist(app: FastAPI) -> None:
    paths = _documented_paths(app)

    assert "/health" in paths
    assert "/api/v1/health" in paths
