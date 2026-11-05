"""Separate worksheet jobs and vendor offers retain their original evidence."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import Principal, RoleCode
from app.imports.storage import LocalStorageBackend
from app.models import Organization, User, Vendor, VendorImportProfile
from app.models.enums import ImportJobStatus, ImportRowStatus, MatchResult
from app.models.ingestion import ImportFile, ImportJobRow
from app.models.inventory import AvailabilityEvent, VendorInventorySnapshot
from app.schemas.import_profiles import ImportProfileCreate
from app.services import imports as import_service
from app.services.import_processing import process_import_job
from app.services.import_profiles import create_profile
from app.services.imports import ImportFileBelongsToAnotherVendor, UploadOutcome, receive_upload
from app.services.matching import match_import_job
from app.services.snapshots import snapshot_import_job
from tests.integration import factories

pytestmark = pytest.mark.integration


@pytest.fixture
def context(db_session: Session) -> tuple[Organization, Vendor, User, Principal]:
    org = factories.make_organization(db_session)
    vendor = factories.make_vendor(db_session, org)
    user = factories.make_user(db_session, org)
    actor = Principal(user.id, org.id, user.email, user.display_name, frozenset({RoleCode.ADMIN}))
    return org, vendor, user, actor


def profile(
    session: Session, vendor: Vendor, actor: Principal, name: str, **selectors: object
) -> VendorImportProfile:
    return create_profile(
        session,
        actor,
        vendor.id,
        ImportProfileCreate.model_validate(
            {
                "name": name,
                "file_format": "XLSX",
                **selectors,
                "column_map": {
                    "columns": [
                        {"target": "vendor_sku", "source": 0},
                        {"target": "description", "source": 1},
                        {"target": "quantity_available", "source": 2},
                        {"target": "unit_cost", "source": 3},
                    ]
                },
                "availability_rules": {"available_when": "listed"},
            }
        ),
    )


def workbook_bytes() -> bytes:
    book = Workbook()
    first = book.active
    assert first is not None
    first.title = "Designer Brands "
    first.append(["SKU", "Description", "Qty", "Cost"])
    first.append(["A", "Designer item", 2, 3])
    other = book.create_sheet("Niche")
    other.append(["SKU", "Description", "Qty", "Cost"])
    other.append(["B", "Niche item", None, 5])
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def test_two_worksheets_share_raw_file_and_cannot_be_imported_twice(
    db_session: Session,
    context: tuple[Organization, Vendor, User, Principal],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org, vendor, _, actor = context
    storage = LocalStorageBackend(tmp_path)
    content = workbook_bytes()
    designer = profile(db_session, vendor, actor, "Designer", sheet_name="Designer Brands ")
    niche = profile(db_session, vendor, actor, "Niche", sheet_name="Niche")
    alias = profile(db_session, vendor, actor, "Designer by index", sheet_index=0)
    resolve_sheet = import_service._selected_sheet

    def retained_then_resolve(selected: VendorImportProfile | None, source: bytes) -> str | None:
        assert any(p.is_file() for p in tmp_path.rglob("*.xlsx")), "raw must precede XLSX reading"
        return resolve_sheet(selected, source)

    monkeypatch.setattr(import_service, "_selected_sheet", retained_then_resolve)

    def upload(selected: VendorImportProfile | None = None) -> UploadOutcome:
        return receive_upload(
            db_session,
            storage,
            principal=actor,
            vendor_id=vendor.id,
            profile_id=selected.id if selected else None,
            filename="stock.xlsx",
            content=content,
            declared_mime=None,
            max_bytes=100000,
        )

    # Keep actual types visible to strict mypy rather than hiding jobs in JSON.
    first = receive_upload(
        db_session,
        storage,
        principal=actor,
        vendor_id=vendor.id,
        profile_id=designer.id,
        filename="stock.xlsx",
        content=content,
        declared_mime=None,
        max_bytes=100000,
    )
    second = receive_upload(
        db_session,
        storage,
        principal=actor,
        vendor_id=vendor.id,
        profile_id=niche.id,
        filename="stock.xlsx",
        content=content,
        declared_mime=None,
        max_bytes=100000,
    )
    assert not first.duplicate and not second.duplicate
    assert first.job.id != second.job.id
    assert first.job.import_file_id == second.job.import_file_id
    assert first.job.source_sheet == "Designer Brands " and second.job.source_sheet == "Niche"
    assert db_session.scalar(select(func.count()).select_from(ImportFile)) == 1
    assert storage.open(first.job.import_file.storage_uri) == content
    duplicate = receive_upload(
        db_session,
        storage,
        principal=actor,
        vendor_id=vendor.id,
        profile_id=alias.id,
        filename="renamed.xlsx",
        content=content,
        declared_mime=None,
        max_bytes=100000,
    )
    assert duplicate.duplicate and duplicate.job.id == first.job.id
    unprofiled = upload()
    assert unprofiled.duplicate and unprofiled.job.id in {first.job.id, second.job.id}
    # The synthetic retained object can be restored even when the re-upload
    # uses a different filename. Both jobs must still reference the original.
    next(tmp_path.rglob("*.xlsx")).unlink()
    restored = receive_upload(
        db_session,
        storage,
        principal=actor,
        vendor_id=vendor.id,
        profile_id=alias.id,
        filename="renamed-again.xlsx",
        content=content,
        declared_mime=None,
        max_bytes=100000,
    )
    assert restored.duplicate and storage.open(first.job.import_file.storage_uri) == content
    for outcome in (first, second):
        process_import_job(
            db_session, storage, organization_id=org.id, job_id=outcome.job.id, actor=actor
        )
        match_import_job(db_session, organization_id=org.id, job_id=outcome.job.id, actor=actor)
        result = snapshot_import_job(
            db_session, organization_id=org.id, job_id=outcome.job.id, actor=actor
        )
        assert result.status is ImportJobStatus.COMPLETED
    assert db_session.scalar(select(func.count()).select_from(VendorInventorySnapshot)) == 2

    other = factories.make_vendor(db_session, org)
    with pytest.raises(ImportFileBelongsToAnotherVendor):
        receive_upload(
            db_session,
            storage,
            principal=actor,
            vendor_id=other.id,
            profile_id=None,
            filename="stock.xlsx",
            content=content,
            declared_mime=None,
            max_bytes=100000,
        )


def test_historical_file_level_job_blocks_new_worksheet_attempt(
    db_session: Session, context: tuple[Organization, Vendor, User, Principal], tmp_path: Path
) -> None:
    org, vendor, _, actor = context
    content = workbook_bytes()
    retained = factories.make_import_file(
        db_session,
        org,
        vendor,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    historical = factories.make_import_job(db_session, org, vendor, retained)
    selected = profile(db_session, vendor, actor, "Niche", sheet_name="Niche")
    outcome = receive_upload(
        db_session,
        LocalStorageBackend(tmp_path),
        principal=actor,
        vendor_id=vendor.id,
        profile_id=selected.id,
        filename="stock.xlsx",
        content=content,
        declared_mime=None,
        max_bytes=100000,
    )
    assert outcome.duplicate and outcome.job.id == historical.id


def test_shared_offers_require_review_and_each_keep_a_snapshot(
    db_session: Session, context: tuple[Organization, Vendor, User, Principal], tmp_path: Path
) -> None:
    org, vendor, _, actor = context
    product = factories.make_product(db_session, org, name="Widget")
    factories.make_identifier(db_session, org, product, normalized_value="00012345678905")
    selected = create_profile(
        db_session,
        actor,
        vendor.id,
        ImportProfileCreate.model_validate(
            {
                "name": "Offers",
                "file_format": "CSV",
                "column_map": {
                    "columns": [
                        {"target": "upc", "source": 0},
                        {"target": "description", "source": 1},
                        {"target": "quantity_available", "source": 2},
                        {"target": "unit_cost", "source": 3},
                        {"target": "offer_variant", "source": 4},
                    ]
                },
                "normalization_rules": {
                    "vendor_sku_from_offer": True,
                    "review_shared_upc_offers": True,
                    "skip_non_product_rows": True,
                },
                "availability_rules": {"available_when": "listed"},
            }
        ),
    )
    content = (
        b"UPC,Description,Qty,Price,Condition\nTESTERS,,,,\n"
        b"012345678905,Widget,12,3,CLEAN\n012345678905,Widget,5,2,DAMAGED\n"
        b",Pouch,,1,CLEAN\n"
    )
    storage = LocalStorageBackend(tmp_path)
    outcome = receive_upload(
        db_session,
        storage,
        principal=actor,
        vendor_id=vendor.id,
        profile_id=selected.id,
        filename="offers.csv",
        content=content,
        declared_mime=None,
        max_bytes=100000,
    )
    process_import_job(
        db_session, storage, organization_id=org.id, job_id=outcome.job.id, actor=actor
    )
    match_import_job(db_session, organization_id=org.id, job_id=outcome.job.id, actor=actor)
    result = snapshot_import_job(
        db_session, organization_id=org.id, job_id=outcome.job.id, actor=actor
    )
    rows = db_session.scalars(select(ImportJobRow).order_by(ImportJobRow.row_number)).all()
    assert result.status is ImportJobStatus.COMPLETED and result.error_rows == 0
    assert rows[0].status is ImportRowStatus.SKIPPED and rows[0].raw_data["upc"] == "TESTERS"
    assert all(r.match_result is not MatchResult.MATCHED for r in rows[1:])
    assert len({r.vendor_product_id for r in rows[1:]}) == 3
    assert all(r.normalized_data.get("upc_shared_by_offers") for r in rows[1:3])
    assert db_session.scalar(select(func.count()).select_from(VendorInventorySnapshot)) == 3
    assert db_session.scalar(select(func.count()).select_from(AvailabilityEvent)) == 3
