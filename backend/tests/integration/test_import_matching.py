"""The matching step of an import against a real catalog (AC-7, AC-8 inputs).

The engine is exercised on its own in ``tests/unit/test_match_engine.py``.
Here: the SQL-built index (including pg_trgm suggestions), what the step
writes to ``import_job_rows``, ``vendor_products`` and
``product_mapping_exceptions``, the approved-mapping guarantees of
CLAUDE.md §5.2, and determinism across two runs.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import RoleCode
from app.db.session import get_db
from app.imports.extract import normalize_vendor_sku
from app.imports.storage import LocalStorageBackend
from app.main import create_app
from app.matching.engine import MatchInput, evaluate
from app.models import AuditEvent, Organization, User, Vendor
from app.models.enums import (
    ExceptionReason,
    ExceptionStatus,
    IdentifierType,
    ImportJobStage,
    ImportJobStatus,
    ImportRowStatus,
    MappingStatus,
    MatchMethod,
    MatchResult,
)
from app.models.ingestion import ImportJob, ImportJobRow
from app.models.matching import ProductMappingException
from app.models.vendor import VendorProduct
from app.repositories.matching import MatchIndexRepository
from app.services.import_processing import process_import_job
from app.services.matching import ImportJobNotAtMatching, match_import_job
from app.services.roles import assign_role, ensure_system_roles
from tests.integration import factories

pytestmark = pytest.mark.integration

IMPORTS = "/api/v1/imports"
UPC_A = "00012345678905"  # 012345678905
UPC_B = "00036000291452"  # 036000291452


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    return tmp_path / "raw"


@pytest.fixture
def api_settings(raw_dir: Path) -> Settings:
    return Settings(
        app_env="test",
        log_level="WARNING",
        dev_auth_enabled=True,
        auth_jwt_secret=SecretStr("integration-test-signing-key-at-least-32-chars"),
        storage_raw_dir=str(raw_dir),
        # Parsing and matching are driven explicitly here.
        import_process_on_upload=False,
    )


@pytest.fixture
def client(db_session: Session, api_settings: Settings) -> Iterator[TestClient]:
    application = create_app(api_settings)
    application.dependency_overrides[get_db] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


@pytest.fixture
def organization(db_session: Session) -> Organization:
    return factories.make_organization(db_session)


@pytest.fixture
def vendor(db_session: Session, organization: Organization) -> Vendor:
    return factories.make_vendor(db_session, organization, code="ACME")


Headers = dict[str, str]
Login = Callable[[RoleCode], tuple[Headers, User]]


@pytest.fixture
def login(client: TestClient, db_session: Session, organization: Organization) -> Login:
    roles = ensure_system_roles(db_session, organization.id)
    sessions: dict[RoleCode, tuple[Headers, User]] = {}

    def _login(role: RoleCode) -> tuple[Headers, User]:
        if role in sessions:
            return sessions[role]
        user = factories.make_user(db_session, organization, email=f"{role.value.lower()}@x.test")
        assign_role(db_session, user=user, role=roles[role])
        db_session.flush()
        token = client.post("/api/v1/auth/dev-token", json={"email": user.email}).json()[
            "access_token"
        ]
        sessions[role] = ({"Authorization": f"Bearer {token}"}, user)
        return sessions[role]

    return _login


@pytest.fixture
def operator(login: Login) -> Headers:
    return login(RoleCode.DATA_OPERATOR)[0]


# --- helpers ---------------------------------------------------------------------------------

HEADERS = ["SKU", "UPC", "Desc", "Qty"]
PROFILE: dict[str, Any] = {
    "name": "match-profile",
    "file_format": "CSV",
    "column_map": {
        "columns": [
            {"target": "vendor_sku", "source": "SKU"},
            {"target": "upc", "source": "UPC"},
            {"target": "description", "source": "Desc"},
            {"target": "quantity_available", "source": "Qty"},
        ]
    },
}


def csv_bytes(rows: list[list[str]], *, headers: list[str] | None = None) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(headers or HEADERS)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def parsed_job(
    client: TestClient,
    operator: Headers,
    vendor: Vendor,
    db_session: Session,
    raw_dir: Path,
    organization: Organization,
    rows: list[list[str]],
    *,
    name: str = "file.csv",
    headers: list[str] | None = None,
) -> ImportJob:
    """Upload and parse; the job is left RUNNING at MATCHING."""
    profile = client.post(
        f"/api/v1/vendors/{vendor.id}/import-profiles", json=PROFILE, headers=operator
    )
    if profile.status_code == 409:  # already created by an earlier call in the test
        listing = client.get(f"/api/v1/vendors/{vendor.id}/import-profiles", headers=operator)
        profile_id = listing.json()["items"][0]["id"]
    else:
        assert profile.status_code == 201, profile.text
        profile_id = profile.json()["id"]
    response = client.post(
        IMPORTS,
        data={"vendor_id": str(vendor.id), "profile_id": profile_id},
        files={"file": (name, csv_bytes(rows, headers=headers))},
        headers=operator,
    )
    assert response.status_code == 201, response.text
    job = process_import_job(
        db_session,
        LocalStorageBackend(raw_dir),
        organization_id=organization.id,
        job_id=uuid.UUID(response.json()["job"]["id"]),
    )
    assert job.status is ImportJobStatus.RUNNING and job.current_stage is ImportJobStage.MATCHING
    return job


def rows_of(db_session: Session, job: ImportJob) -> dict[str, ImportJobRow]:
    return {
        r.vendor_sku or f"row-{r.row_number}": r
        for r in db_session.execute(
            select(ImportJobRow).where(ImportJobRow.import_job_id == job.id)
        ).scalars()
    }


def items_of(db_session: Session, job: ImportJob) -> list[ProductMappingException]:
    return list(
        db_session.execute(
            select(ProductMappingException)
            .where(ProductMappingException.import_job_id == job.id)
            .order_by(ProductMappingException.created_at)
        ).scalars()
    )


def line_of(db_session: Session, vendor: Vendor, sku: str) -> VendorProduct:
    return db_session.execute(
        select(VendorProduct).where(
            VendorProduct.vendor_id == vendor.id,
            VendorProduct.normalized_vendor_sku == normalize_vendor_sku(sku),
        )
    ).scalar_one()


def approved_line(
    db_session: Session, organization: Organization, vendor: Vendor, sku: str, product_id: uuid.UUID
) -> VendorProduct:
    return factories.make_vendor_product(
        db_session,
        organization,
        vendor,
        vendor_sku=sku,
        normalized_vendor_sku=normalize_vendor_sku(sku),
        product_id=product_id,
        mapping_status=MappingStatus.APPROVED,
        mapping_method=MatchMethod.MANUAL_APPROVAL,
        mapping_approved_at=datetime.now(UTC),
    )


# --- the index ---------------------------------------------------------------------------------


class TestMatchIndex:
    def test_only_what_the_rules_may_read_goes_in(
        self, db_session: Session, organization: Organization, vendor: Vendor
    ) -> None:
        p1 = factories.make_product(db_session, organization, catalog_item_number="cin-1")
        p2 = factories.make_product(
            db_session, organization, catalog_item_number="CIN-2", is_active=False
        )
        factories.make_identifier(db_session, organization, p1, normalized_value=UPC_A)
        factories.make_identifier(
            db_session, organization, p2, normalized_value=UPC_B, has_valid_checksum=False
        )
        factories.make_identifier(
            db_session, organization, p1, normalized_value="X", is_active=False
        )
        approved_line(db_session, organization, vendor, "APP-1", p1.id)
        factories.make_vendor_product(
            db_session,
            organization,
            vendor,
            vendor_sku="PEND-1",
            normalized_vendor_sku="PEND-1",
            product_id=p1.id,
            mapping_status=MappingStatus.PENDING,
        )
        other_vendor = factories.make_vendor(db_session, organization, code="OTHER")
        approved_line(db_session, organization, other_vendor, "APP-2", p1.id)
        approver = factories.make_user(db_session, organization, email="approver@x.test")
        factories.make_marketplace_listing(
            db_session,
            organization,
            p1,
            seller_sku="AMZ-OK",
            mapping_status=MappingStatus.APPROVED,
            mapping_method=MatchMethod.MANUAL_APPROVAL,
            approved_by_user_id=approver.id,
            approved_at=datetime.now(UTC),
        )
        factories.make_marketplace_listing(db_session, organization, p1, seller_sku="AMZ-PEND")
        foreign = factories.make_organization(db_session)
        foreign_product = factories.make_product(db_session, foreign, catalog_item_number="CIN-1")
        factories.make_identifier(db_session, foreign, foreign_product, normalized_value=UPC_A)

        index = MatchIndexRepository(db_session, organization.id).build(vendor.id)

        assert index.by_upc == {UPC_A: (p1.id,)}  # bad checksum, inactive, other org excluded
        assert index.by_catalog_item_number == {"CIN-1": (p1.id,)}  # inactive product excluded
        assert index.by_vendor_sku == {
            (vendor.id, "APP-1"): (p1.id,)
        }  # pending, other vendor excluded
        assert index.by_amazon_sku == {"AMZ-OK": (p1.id,)}

    def test_suggestions_come_from_trigram_similarity_scored_and_ordered(
        self, db_session: Session, organization: Organization, vendor: Vendor
    ) -> None:
        widget = factories.make_product(db_session, organization, name="Blue Widget 12 pack")
        gadget = factories.make_product(db_session, organization, name="Blue Gadget 12 pack")
        factories.make_product(db_session, organization, name="Unrelated Thing")
        foreign = factories.make_organization(db_session)
        factories.make_product(db_session, foreign, name="Blue Widget 12 pack")

        suggestions = MatchIndexRepository(db_session, organization.id).suggest_by_description(
            "Blue Widget 12 pack"
        )

        assert [s.product_id for s in suggestions] == [widget.id, gadget.id]
        assert suggestions[0].score == 1.0 and 0.3 <= suggestions[1].score < 1.0

        outcome = evaluate(
            MatchInput(description="Blue Widget 12 pack"),
            MatchIndexRepository(db_session, organization.id).build(vendor.id),
        )
        assert outcome.result is MatchResult.SUGGESTION_ONLY
        assert outcome.candidates[0] == widget.id and outcome.product_id is None


# --- the step -----------------------------------------------------------------------------------


class TestMatchImportJob:
    def test_every_outcome_is_recorded_on_rows_lines_and_the_queue(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        by_upc = factories.make_product(db_session, organization, name="Blue Widget")
        twin_a = factories.make_product(db_session, organization, name="Twin A")
        twin_b = factories.make_product(db_session, organization, name="Twin B")
        by_sku = factories.make_product(db_session, organization, name="Mapped Product")
        named = factories.make_product(db_session, organization, name="Green Gadget 6 pack")
        factories.make_identifier(db_session, organization, by_upc, normalized_value=UPC_A)
        factories.make_identifier(db_session, organization, twin_a, normalized_value=UPC_B)
        # One value may sit on two products only under different identifier
        # types (uq_product_identifiers_global_value); the chain still sees two.
        factories.make_identifier(
            db_session,
            organization,
            twin_b,
            normalized_value=UPC_B,
            identifier_type=IdentifierType.GTIN,
        )
        approved_line(db_session, organization, vendor, "MAPPED-1", by_sku.id)
        db_session.flush()

        job = parsed_job(
            client,
            operator,
            vendor,
            db_session,
            raw_dir,
            organization,
            [
                ["NEW-1", "012345678905", "Blue Widget", "5"],  # UPC → MATCHED, pending mapping
                ["AMB-1", "036000291452", "Twin", "5"],  # two products → AMBIGUOUS
                ["MAPPED-1", "", "Mapped Product", "5"],  # approved SKU → MATCHED p3
                ["SUG-1", "", "Green Gadget 6 pack", "5"],  # name only → SUGGESTION_ONLY
                ["NONE-1", "", "Mystery item", "5"],  # nothing → UNMATCHED
                ["DUP-1", "012345678905", "First", "1"],  # superseded by the next row
                ["dup-1", "012345678905", "Last", "2"],  # last occurrence carries the line
                [
                    "BAD-1",
                    "012345678906",
                    "Bad check digit",
                    "3",
                ],  # UPC_INVALID → not an identifier
            ],
        )

        matched = match_import_job(db_session, organization_id=organization.id, job_id=job.id)

        assert matched.status is ImportJobStatus.RUNNING
        assert matched.current_stage is ImportJobStage.SNAPSHOTTING
        rows = rows_of(db_session, job)

        new = rows["NEW-1"]
        assert (new.match_result, new.matched_by, new.match_priority, new.product_id) == (
            MatchResult.MATCHED,
            MatchMethod.UPC,
            1,
            by_upc.id,
        )
        line = line_of(db_session, vendor, "NEW-1")
        assert line.mapping_status is MappingStatus.PENDING and line.product_id == by_upc.id
        assert line.mapping_method is MatchMethod.UPC
        assert new.vendor_product_id == line.id
        assert new.normalized_data["match"]["evaluations"][0]["outcome"] == "matched"

        amb = rows["AMB-1"]
        assert amb.match_result is MatchResult.AMBIGUOUS and amb.product_id is None
        assert amb.match_priority == 1 and amb.matched_by is None
        assert line_of(db_session, vendor, "AMB-1").mapping_status is MappingStatus.UNMAPPED

        mapped = rows["MAPPED-1"]
        assert (mapped.match_result, mapped.matched_by, mapped.match_priority) == (
            MatchResult.MATCHED,
            MatchMethod.VENDOR_SKU_MAPPING,
            3,
        )
        assert mapped.product_id == by_sku.id
        assert line_of(db_session, vendor, "MAPPED-1").mapping_status is MappingStatus.APPROVED

        sug = rows["SUG-1"]
        assert sug.match_result is MatchResult.SUGGESTION_ONLY and sug.product_id is None
        assert sug.normalized_data["match"]["suggestions"][0]["product_id"] == str(named.id)

        none = rows["NONE-1"]
        assert none.match_result is MatchResult.UNMATCHED and none.product_id is None

        first, last = rows["DUP-1"], rows["dup-1"]
        assert first.match_result is None  # superseded: not matched, no line
        assert last.match_result is MatchResult.MATCHED and last.product_id == by_upc.id

        bad = rows["BAD-1"]
        assert bad.status is ImportRowStatus.WARNING
        assert bad.match_result is MatchResult.UNMATCHED
        assert bad.normalized_data["match"]["evaluations"][0]["outcome"] == "skipped"

        items = {
            i.vendor_product.normalized_vendor_sku: i
            for i in items_of(db_session, job)
            if i.vendor_product is not None
        }
        assert set(items) == {"NEW-1", "AMB-1", "SUG-1", "NONE-1", "DUP-1", "BAD-1"}
        assert items["NEW-1"].reason is ExceptionReason.SUGGESTION_ONLY
        assert items["NEW-1"].suggested_product_id == by_upc.id
        assert items["AMB-1"].reason is ExceptionReason.AMBIGUOUS_MATCH
        assert {c["product_id"] for c in items["AMB-1"].candidates["products"]} == {
            str(twin_a.id),
            str(twin_b.id),
        }
        assert items["SUG-1"].reason is ExceptionReason.SUGGESTION_ONLY
        assert items["SUG-1"].suggested_product_id == named.id
        assert items["SUG-1"].suggestion_score is not None and items["SUG-1"].suggestion_score > 0
        assert items["NONE-1"].reason is ExceptionReason.NO_MATCH
        assert items["NONE-1"].candidates == {"products": []}
        assert [r["priority"] for r in items["NONE-1"].match_evaluations["rules"]] == [
            1,
            2,
            3,
            4,
            5,
        ]
        assert all(i.status is ExceptionStatus.PENDING for i in items.values())
        assert all(i.import_job_row_id is not None for i in items.values())

        assert matched.matched_rows == 3  # NEW-1, MAPPED-1, dup-1
        assert matched.exception_rows == 4  # AMB-1, SUG-1, NONE-1, BAD-1
        summary = matched.error_details["matching"]
        assert summary["by_result"] == {
            "MATCHED": 3,
            "AMBIGUOUS": 1,
            "UNMATCHED": 2,
            "SUGGESTION_ONLY": 1,
        }
        assert summary["superseded_skipped"] == 1
        assert summary["mapping_suggestions"] == 2  # NEW-1 and dup-1
        assert summary["exceptions_opened"] == 6

        actions = sorted(
            db_session.execute(
                select(AuditEvent.action).where(AuditEvent.entity_id == job.id)
            ).scalars()
        )
        assert "import_job.matched" in actions

        report = client.get(f"{IMPORTS}/{job.id}/report", headers=operator).json()
        assert report["counters"]["matched"] == 3 and report["counters"]["exception"] == 4
        assert report["current_stage"] == "SNAPSHOTTING"

    def test_an_approved_mapping_is_never_touched_and_a_disagreement_is_a_conflict(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        approved_product = factories.make_product(db_session, organization, name="Approved")
        upc_product = factories.make_product(db_session, organization, name="By UPC")
        factories.make_identifier(db_session, organization, upc_product, normalized_value=UPC_A)
        line = approved_line(db_session, organization, vendor, "SKU-1", approved_product.id)
        agree = approved_line(db_session, organization, vendor, "SKU-2", upc_product.id)
        db_session.flush()

        job = parsed_job(
            client,
            operator,
            vendor,
            db_session,
            raw_dir,
            organization,
            [["SKU-1", "012345678905", "x", "1"], ["SKU-2", "012345678905", "y", "1"]],
        )
        match_import_job(db_session, organization_id=organization.id, job_id=job.id)

        db_session.refresh(line)
        db_session.refresh(agree)
        # The chain answers by UPC (priority 1 outranks 3); the approval stands untouched.
        rows = rows_of(db_session, job)
        assert rows["SKU-1"].product_id == upc_product.id
        assert rows["SKU-1"].matched_by is MatchMethod.UPC
        assert line.mapping_status is MappingStatus.APPROVED
        assert line.product_id == approved_product.id
        [item] = items_of(db_session, job)
        assert item.reason is ExceptionReason.CONFLICTING_IDENTIFIER
        assert item.vendor_product_id == line.id
        # Agreement: nothing to ask.
        assert rows["SKU-2"].product_id == upc_product.id
        assert agree.mapping_status is MappingStatus.APPROVED

    def test_a_second_import_refreshes_open_items_instead_of_duplicating_them(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        first = parsed_job(
            client,
            operator,
            vendor,
            db_session,
            raw_dir,
            organization,
            [["NONE-1", "", "Mystery", "1"]],
            name="a.csv",
        )
        match_import_job(db_session, organization_id=organization.id, job_id=first.id)
        second = parsed_job(
            client,
            operator,
            vendor,
            db_session,
            raw_dir,
            organization,
            [["NONE-1", "", "Mystery", "2"]],
            name="b.csv",
        )
        match_import_job(db_session, organization_id=organization.id, job_id=second.id)

        items = list(
            db_session.execute(
                select(ProductMappingException).where(
                    ProductMappingException.vendor_id == vendor.id
                )
            ).scalars()
        )
        assert len(items) == 1
        assert items[0].import_job_id == second.id
        assert second.error_details["matching"]["exceptions_refreshed"] == 1
        assert (
            len(
                db_session.execute(
                    select(VendorProduct).where(VendorProduct.vendor_id == vendor.id)
                )
                .scalars()
                .all()
            )
            == 1
        )

    def test_only_a_running_job_at_matching_can_be_matched(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        job = parsed_job(
            client, operator, vendor, db_session, raw_dir, organization, [["S", "", "x", "1"]]
        )
        match_import_job(db_session, organization_id=organization.id, job_id=job.id)

        with pytest.raises(ImportJobNotAtMatching):
            match_import_job(db_session, organization_id=organization.id, job_id=job.id)

    def test_upload_runs_parse_then_match_when_processing_on_upload(
        self, db_session: Session, raw_dir: Path, organization: Organization, vendor: Vendor
    ) -> None:
        settings = Settings(
            app_env="test",
            log_level="WARNING",
            dev_auth_enabled=True,
            auth_jwt_secret=SecretStr("integration-test-signing-key-at-least-32-chars"),
            storage_raw_dir=str(raw_dir),
            import_process_on_upload=True,
        )
        application = create_app(settings)
        application.dependency_overrides[get_db] = lambda: db_session
        roles = ensure_system_roles(db_session, organization.id)
        user = factories.make_user(db_session, organization, email="op@x.test")
        assign_role(db_session, user=user, role=roles[RoleCode.DATA_OPERATOR])
        db_session.flush()
        product = factories.make_product(db_session, organization)
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        with TestClient(application) as client:
            token = client.post("/api/v1/auth/dev-token", json={"email": user.email}).json()[
                "access_token"
            ]
            headers = {"Authorization": f"Bearer {token}"}
            profile_id = client.post(
                f"/api/v1/vendors/{vendor.id}/import-profiles", json=PROFILE, headers=headers
            ).json()["id"]

            response = client.post(
                IMPORTS,
                data={"vendor_id": str(vendor.id), "profile_id": profile_id},
                files={"file": ("f.csv", csv_bytes([["S-1", "012345678905", "x", "1"]]))},
                headers=headers,
            )

        application.dependency_overrides.clear()
        body = response.json()["job"]
        assert body["status"] == "RUNNING" and body["current_stage"] == "SNAPSHOTTING"
        assert body["matched_rows"] == 1 and body["exception_rows"] == 0


# --- determinism (AC-7.5) ---------------------------------------------------------------------


class TestDeterminism:
    def test_the_same_job_matched_twice_against_the_same_state_is_identical(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        p1 = factories.make_product(db_session, organization, name="Blue Widget 12 pack")
        p2 = factories.make_product(db_session, organization, name="Twin A")
        p3 = factories.make_product(db_session, organization, name="Twin B")
        factories.make_identifier(db_session, organization, p1, normalized_value=UPC_A)
        factories.make_identifier(db_session, organization, p2, normalized_value=UPC_B)
        factories.make_identifier(
            db_session,
            organization,
            p3,
            normalized_value=UPC_B,
            identifier_type=IdentifierType.GTIN,
        )
        approved_line(db_session, organization, vendor, "MAPPED-1", p1.id)
        db_session.flush()
        rows = [
            ["NEW-1", "012345678905", "Blue Widget", "5"],
            ["AMB-1", "036000291452", "Twin", "5"],
            ["MAPPED-1", "", "Mapped", "5"],
            ["SUG-1", "", "Blue Widget 12 pack", "5"],
            ["NONE-1", "", "Mystery", "5"],
        ]

        def attribution(job: ImportJob) -> list[dict[str, Any]]:
            return [
                {
                    "row_number": r.row_number,
                    "result": r.match_result.value if r.match_result else None,
                    "method": r.matched_by.value if r.matched_by else None,
                    "priority": r.match_priority,
                    "product_id": str(r.product_id) if r.product_id else None,
                    "trail": r.normalized_data.get("match"),
                }
                for r in sorted(rows_of(db_session, job).values(), key=lambda r: r.row_number)
            ]

        def queue(job: ImportJob) -> list[dict[str, Any]]:
            return [
                {
                    "sku": i.vendor_product.normalized_vendor_sku if i.vendor_product else None,
                    "reason": i.reason.value,
                    "suggested": str(i.suggested_product_id) if i.suggested_product_id else None,
                    "score": str(i.suggestion_score),
                    "candidates": i.candidates,
                    "evaluations": i.match_evaluations,
                }
                for i in sorted(
                    items_of(db_session, job), key=lambda i: i.vendor_product_id or uuid.UUID(int=0)
                )
            ]

        first = parsed_job(
            client, operator, vendor, db_session, raw_dir, organization, rows, name="run-1.csv"
        )
        match_import_job(db_session, organization_id=organization.id, job_id=first.id)
        first_rows, first_queue = attribution(first), queue(first)

        # Same bytes cannot be uploaded twice (dedupe); a header-only change gives
        # a different file whose parsed rows are identical.
        second = parsed_job(
            client,
            operator,
            vendor,
            db_session,
            raw_dir,
            organization,
            rows,
            name="run-2.csv",
            headers=[h.lower() for h in HEADERS],
        )
        # The first run only added PENDING lines and open items — no APPROVED
        # mapping changed, so the mapping state the rules read is the same.
        match_import_job(db_session, organization_id=organization.id, job_id=second.id)

        assert attribution(second) == first_rows
        assert queue(second) == first_queue
        assert second.matched_rows == first.matched_rows == 2
        assert second.exception_rows == first.exception_rows == 3
