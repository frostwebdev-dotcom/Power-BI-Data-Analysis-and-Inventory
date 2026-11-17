"""Explicit replacement reasons, bounded files and early authentication/role checks."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.imports.storage import LocalStorageBackend
from app.schemas.financial_imports import FinancialReplacementRecordRequest
from app.services.financial_imports import FinancialInputInvalid
from app.services.financial_replacements import preview_replacement, record_replacement


@pytest.mark.parametrize("reason", ["", "short", " " * 20, "x" * 1001, "some reason\nwith lines"])
def test_replacement_reason_is_explicit_bounded_and_nonblank(reason: str) -> None:
    with pytest.raises(ValidationError):
        FinancialReplacementRecordRequest(preview_sha256="a" * 64, reason=reason)


def test_replacement_request_does_not_accept_relabelled_context() -> None:
    request = FinancialReplacementRecordRequest(
        preview_sha256="a" * 64, reason="  Correct the reported product costs.  "
    )
    assert request.reason == "Correct the reported product costs."
    for changes in ({"preview_sha256": "bad"}, {"reporting_year": 2026}, {"store_name": "Other"}):
        with pytest.raises(ValidationError):
            FinancialReplacementRecordRequest.model_validate({**request.model_dump(), **changes})


@pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.PURCHASING_MANAGER])
def test_replacement_services_refuse_nonoperators_before_storage_or_database(
    tmp_path: Path, role: RoleCode
) -> None:
    actor = Principal(
        uuid.uuid4(), uuid.uuid4(), "reader@example.test", "Reader", frozenset({role})
    )
    session = cast(Session, object())
    storage = LocalStorageBackend(tmp_path)
    with pytest.raises(AuthorizationError):
        preview_replacement(session, storage, actor, uuid.uuid4(), "report.xlsx", b"test", 1, 25)
    with pytest.raises(AuthorizationError):
        record_replacement(
            session,
            storage,
            actor,
            uuid.uuid4(),
            "report.xlsx",
            b"test",
            FinancialReplacementRecordRequest(
                preview_sha256="a" * 64, reason="Correct source costs"
            ),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name,content",
    [("report.csv", b"test"), ("empty.xlsx", b""), ("big.xlsx", b"x" * (8 * 1024 * 1024 + 1))],
)
def test_record_rejects_unsupported_file_before_database(
    tmp_path: Path, name: str, content: bytes
) -> None:
    actor = Principal(
        uuid.uuid4(), uuid.uuid4(), "operator@example.test", "Operator", frozenset({RoleCode.ADMIN})
    )
    with pytest.raises(FinancialInputInvalid):
        record_replacement(
            cast(Session, object()),
            LocalStorageBackend(tmp_path),
            actor,
            uuid.uuid4(),
            name,
            content,
            FinancialReplacementRecordRequest(
                preview_sha256="a" * 64, reason="Correct source costs"
            ),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("suffix", ["replacement-preview", "replacements"])
def test_replacement_routes_require_authentication(client: TestClient, suffix: str) -> None:
    assert client.post(f"/api/v1/financial-imports/{uuid.uuid4()}/{suffix}").status_code == 401
