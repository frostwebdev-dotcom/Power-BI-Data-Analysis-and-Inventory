"""Reporting context and refusal paths that must precede any financial write."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal, get_storage
from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.imports.storage import LocalStorageBackend, sha256_hex
from app.models.financial import FinancialImport
from app.services.financial_imports import (
    FinancialContextMissing,
    FinancialImportConflict,
    FinancialInputInvalid,
    open_financial_raw,
    receive_financial_import,
    reporting_bounds,
)
from tests.unit.test_connectbooks import profile


@pytest.mark.parametrize(
    "month,year,start,end",
    [
        (10, 2026, datetime(2026, 10, 1, 4, tzinfo=UTC), datetime(2026, 11, 1, 4, tzinfo=UTC)),
        (11, 2026, datetime(2026, 11, 1, 4, tzinfo=UTC), datetime(2026, 12, 1, 5, tzinfo=UTC)),
        (12, 2026, datetime(2026, 12, 1, 5, tzinfo=UTC), datetime(2027, 1, 1, 5, tzinfo=UTC)),
        (2, 2024, datetime(2024, 2, 1, 5, tzinfo=UTC), datetime(2024, 3, 1, 5, tzinfo=UTC)),
    ],
)
def test_calendar_month_bounds_preserve_dst_leap_year_and_year_rollover(
    month: int, year: int, start: datetime, end: datetime
) -> None:
    assert reporting_bounds(profile(reporting_month=month, reporting_year=year)) == (start, end)
    if month == 11:
        assert end - start == timedelta(days=30, hours=1)


def test_ambiguous_midnight_is_rejected_instead_of_picking_a_fold() -> None:
    with pytest.raises(FinancialInputInvalid):
        reporting_bounds(
            profile(reporting_year=2020, reporting_month=11, reporting_timezone="America/Havana")
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"reporting_year": None},
        {"reporting_timezone": None},
        {"reporting_year": None, "reporting_timezone": None},
    ],
)
def test_missing_context_does_not_touch_storage_or_database(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    actor = Principal(
        uuid.uuid4(), uuid.uuid4(), "operator@example.test", "Operator", frozenset({RoleCode.ADMIN})
    )
    with pytest.raises(FinancialContextMissing):
        receive_financial_import(
            cast(Session, object()),
            LocalStorageBackend(tmp_path),
            principal=actor,
            filename="report.xlsx",
            content=b"not read",
            profile=profile(**changes),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "filename", ["file.csv", "", "../", "bad\r\nname.xlsx", "a" * 256 + ".xlsx"]
)
def test_bad_filenames_are_refused_before_retention(tmp_path: Path, filename: str) -> None:
    actor = Principal(
        uuid.uuid4(), uuid.uuid4(), "operator@example.test", "Operator", frozenset({RoleCode.ADMIN})
    )
    with pytest.raises(FinancialInputInvalid):
        receive_financial_import(
            cast(Session, object()),
            LocalStorageBackend(tmp_path),
            principal=actor,
            filename=filename,
            content=b"not read",
            profile=profile(),
        )
    assert list(tmp_path.iterdir()) == []


def test_viewer_cannot_call_the_mutation_service_directly(tmp_path: Path) -> None:
    actor = Principal(
        uuid.uuid4(), uuid.uuid4(), "viewer@example.test", "Viewer", frozenset({RoleCode.VIEWER})
    )
    with pytest.raises(AuthorizationError):
        receive_financial_import(
            cast(Session, object()),
            LocalStorageBackend(tmp_path),
            principal=actor,
            filename="report.xlsx",
            content=b"not read",
            profile=profile(),
        )
    assert list(tmp_path.iterdir()) == []


def test_raw_download_checks_integrity_and_never_returns_corrupt_bytes(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path)
    raw = b"original retained bytes"
    uri = storage.put(raw, "report.xlsx", organization_id=uuid.uuid4())
    batch = FinancialImport(storage_uri=uri, raw_sha256=sha256_hex(raw))
    assert open_financial_raw(storage, batch) == raw
    path = next(tmp_path.rglob("*.xlsx"))
    path.write_bytes(b"corrupted bytes")
    with pytest.raises(FinancialImportConflict):
        open_financial_raw(storage, batch)


@pytest.mark.parametrize(
    "payload",
    [
        '{"currency":"USD","currency":"EUR"}',
        '{"private_input":"do-not-echo"}',
        "{invalid",
    ],
)
def test_invalid_profile_errors_do_not_echo_input(
    healthy_db_app: FastAPI, tmp_path: Path, payload: str
) -> None:
    actor = Principal(
        uuid.uuid4(), uuid.uuid4(), "operator@example.test", "Operator", frozenset({RoleCode.ADMIN})
    )
    healthy_db_app.dependency_overrides[get_current_principal] = lambda: actor
    healthy_db_app.dependency_overrides[get_storage] = lambda: LocalStorageBackend(tmp_path)
    with TestClient(healthy_db_app) as client:
        response = client.post(
            "/api/v1/financial-imports",
            files={"file": ("report.xlsx", b"not parsed")},
            data={"profile_json": payload},
        )
    assert response.status_code == 422 and "do-not-echo" not in response.text
    assert list(tmp_path.iterdir()) == []


def test_http_upload_needs_confirmed_context_before_storage(
    healthy_db_app: FastAPI, tmp_path: Path
) -> None:
    actor = Principal(
        uuid.uuid4(), uuid.uuid4(), "operator@example.test", "Operator", frozenset({RoleCode.ADMIN})
    )
    healthy_db_app.dependency_overrides[get_current_principal] = lambda: actor
    healthy_db_app.dependency_overrides[get_storage] = lambda: LocalStorageBackend(tmp_path)
    with TestClient(healthy_db_app) as client:
        response = client.post(
            "/api/v1/financial-imports",
            files={"file": ("report.xlsx", b"not parsed")},
            data={"profile_json": profile(reporting_year=None).model_dump_json()},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "financial_reporting_context_required"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "/00000000-0000-0000-0000-000000000001",
        "/00000000-0000-0000-0000-000000000001/rows",
        "/00000000-0000-0000-0000-000000000001/raw",
    ],
)
def test_financial_read_routes_require_authentication(client: TestClient, suffix: str) -> None:
    assert client.get("/api/v1/financial-imports" + suffix).status_code == 401
