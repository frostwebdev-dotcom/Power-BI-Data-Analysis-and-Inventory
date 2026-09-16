"""The exception queue end to end (AC-8.1 to AC-8.7; Milestone 1 exit criterion 7).

The decisive test is the re-import: a row lands in the queue, a person
approves it, the same file is imported again, and the row matches at
priority 3 with no new exception.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
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
from app.main import create_app
from app.models import AuditEvent, Organization, User, Vendor
from app.models.enums import MappingStatus
from app.models.ingestion import ImportJob, ImportJobRow
from app.models.matching import ProductMappingException
from app.models.vendor import VendorProduct
from app.services.roles import assign_role, ensure_system_roles
from tests.integration import factories

pytestmark = pytest.mark.integration

EXCEPTIONS = "/api/v1/exceptions"
IMPORTS = "/api/v1/imports"
UPC_A = "00012345678905"


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
        import_process_on_upload=True,
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


@pytest.fixture
def manager(login: Login) -> tuple[Headers, User]:
    return login(RoleCode.PURCHASING_MANAGER)


# --- helpers -------------------------------------------------------------------------------

HEADERS = ["SKU", "UPC", "Desc", "Qty"]
PROFILE: dict[str, Any] = {
    "name": "queue-profile",
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


def import_rows(
    client: TestClient,
    operator: Headers,
    vendor: Vendor,
    rows: list[list[str]],
    *,
    name: str = "file.csv",
    headers: list[str] | None = None,
) -> dict[str, Any]:
    """Upload with the profile; the hook parses and matches inside the request."""
    profiles = client.get(f"/api/v1/vendors/{vendor.id}/import-profiles", headers=operator).json()
    if profiles["total"]:
        profile_id = profiles["items"][0]["id"]
    else:
        created = client.post(
            f"/api/v1/vendors/{vendor.id}/import-profiles", json=PROFILE, headers=operator
        )
        assert created.status_code == 201, created.text
        profile_id = created.json()["id"]
    response = client.post(
        IMPORTS,
        data={"vendor_id": str(vendor.id), "profile_id": profile_id},
        files={"file": (name, csv_bytes(rows, headers=headers))},
        headers=operator,
    )
    assert response.status_code == 201, response.text
    job: dict[str, Any] = response.json()["job"]
    assert job["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS"), job
    return job


def queue(client: TestClient, headers: Headers, **params: Any) -> dict[str, Any]:
    response = client.get(EXCEPTIONS, params=params, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def the_item(client: TestClient, headers: Headers, vendor_sku: str) -> dict[str, Any]:
    items = [
        i for i in queue(client, headers, page_size=200)["items"] if i["vendor_sku"] == vendor_sku
    ]
    assert len(items) == 1, items
    item: dict[str, Any] = items[0]
    return item


def row_of(db_session: Session, job_id: str, sku: str) -> ImportJobRow:
    return db_session.execute(
        select(ImportJobRow).where(
            ImportJobRow.import_job_id == uuid.UUID(job_id), ImportJobRow.vendor_sku == sku
        )
    ).scalar_one()


def line_of(db_session: Session, vendor: Vendor, sku: str) -> VendorProduct:
    return db_session.execute(
        select(VendorProduct).where(
            VendorProduct.vendor_id == vendor.id, VendorProduct.normalized_vendor_sku == sku.upper()
        )
    ).scalar_one()


def audit_rows(db_session: Session, entity_id: uuid.UUID) -> list[AuditEvent]:
    return sorted(
        db_session.execute(select(AuditEvent).where(AuditEvent.entity_id == entity_id)).scalars(),
        key=lambda e: e.action,
    )


# --- exit criterion 7: approve, re-import, matched at priority 3 ---------------------------


class TestReimportAfterApproval:
    def test_an_approved_exception_is_used_automatically_on_the_next_import(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, approver = manager
        product = factories.make_product(db_session, organization, name="Blue Widget")
        rows = [["ACM-001", "", "Blue Widget 12 pack", "5"]]  # no identifier: nothing can match

        # 1. First import: the row lands in the queue.
        first = import_rows(client, operator, vendor, rows, name="monday.csv")
        assert first["matched_rows"] == 0 and first["exception_rows"] == 1
        item = the_item(client, headers, "ACM-001")
        assert item["reason"] == "SUGGESTION_ONLY" and item["status"] == "PENDING"
        assert item["row_number"] == 2 and item["description"] == "Blue Widget 12 pack"
        detail = client.get(f"{EXCEPTIONS}/{item['id']}", headers=headers).json()
        assert detail["source_row"]["raw_data"]["vendor_sku"] == "ACM-001"
        assert [c["product_id"] for c in detail["candidates"]] == [str(product.id)]
        assert detail["candidates"][0]["product"]["name"] == "Blue Widget"
        assert detail["candidates"][0]["score"] is not None
        assert [r["priority"] for r in detail["match_evaluations"]["rules"]] == [1, 2, 3, 4, 5]
        assert detail["vendor_line"]["mapping_status"] == "UNMAPPED"

        # 2. A purchasing manager approves it.
        response = client.post(
            f"{EXCEPTIONS}/{item['id']}/approve",
            json={"product_id": str(product.id), "note": "It is the 12 pack."},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["superseded_product_id"] is None
        assert body["exception"]["status"] == "APPROVED"
        assert body["exception"]["resolved_product_id"] == str(product.id)
        assert body["exception"]["resolved_by_user_id"] == str(approver.id)
        assert body["exception"]["resolved_at"] is not None
        assert body["exception"]["resolution_note"] == "It is the 12 pack."
        assert body["exception"]["vendor_line"]["mapping_status"] == "APPROVED"
        line = line_of(db_session, vendor, "ACM-001")
        assert line.product_id == product.id
        assert line.mapping_status is MappingStatus.APPROVED
        assert line.mapping_method.value == "MANUAL_APPROVAL"  # type: ignore[union-attr]
        assert line.mapping_approved_by_user_id == approver.id
        assert line.mapping_approved_at is not None and line.mapping_approved_at.tzinfo is not None
        row = row_of(db_session, first["id"], "ACM-001")
        assert row.match_result is not None and row.match_result.value == "MATCHED"
        assert row.matched_by is not None and row.matched_by.value == "MANUAL_APPROVAL"
        assert row.match_priority == 5 and row.product_id == product.id
        job = db_session.get(ImportJob, uuid.UUID(first["id"]))
        assert job is not None and (job.matched_rows, job.exception_rows) == (1, 0)
        assert queue(client, headers)["total"] == 0

        # 3. The same file again (different bytes: header case) — priority 3, no exception.
        second = import_rows(
            client, operator, vendor, rows, name="tuesday.csv", headers=[h.lower() for h in HEADERS]
        )
        assert second["matched_rows"] == 1 and second["exception_rows"] == 0
        row = row_of(db_session, second["id"], "ACM-001")
        assert row.matched_by is not None and row.matched_by.value == "VENDOR_SKU_MAPPING"
        assert row.match_priority == 3 and row.product_id == product.id
        trail = row.normalized_data["match"]["evaluations"]
        assert [(e["rule"], e["outcome"]) for e in trail] == [
            ("UPC", "skipped"),
            ("CATALOG_ITEM_NUMBER", "skipped"),
            ("VENDOR_SKU_MAPPING", "matched"),
            ("AMAZON_SKU_MAPPING", "skipped"),
        ]
        assert queue(client, headers)["total"] == 0
        assert (
            db_session.execute(
                select(ProductMappingException).where(
                    ProductMappingException.import_job_id == uuid.UUID(second["id"])
                )
            )
            .scalars()
            .all()
            == []
        )
        # The approval is still the only mapping and still approved by the same person.
        db_session.refresh(line)
        assert line.mapping_approved_by_user_id == approver.id


# --- access control -------------------------------------------------------------------------


class TestAccessControl:
    @pytest.mark.parametrize(
        ("method", "suffix"),
        [
            ("GET", ""),
            ("GET", f"/{uuid.uuid4()}"),
            ("POST", f"/{uuid.uuid4()}/approve"),
            ("POST", f"/{uuid.uuid4()}/reject"),
            ("POST", f"/{uuid.uuid4()}/defer"),
        ],
    )
    def test_every_route_requires_authentication(
        self, client: TestClient, method: str, suffix: str
    ) -> None:
        response = client.request(method, EXCEPTIONS + suffix, json={})
        assert response.status_code == 401

    @pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.DATA_OPERATOR])
    def test_viewers_and_operators_read_but_do_not_decide(
        self,
        client: TestClient,
        login: Login,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
        role: RoleCode,
    ) -> None:
        product = factories.make_product(db_session, organization)
        import_rows(client, operator, vendor, [["S-1", "", "x", "1"]])
        headers, _ = login(role)
        item = the_item(client, headers, "S-1")

        assert client.get(f"{EXCEPTIONS}/{item['id']}", headers=headers).status_code == 200
        for suffix, body in [
            ("approve", {"product_id": str(product.id)}),
            ("reject", {"note": "no"}),
            ("defer", {"until": (datetime.now(UTC) + timedelta(days=1)).isoformat()}),
        ]:
            response = client.post(
                f"{EXCEPTIONS}/{item['id']}/{suffix}", json=body, headers=headers
            )
            assert response.status_code == 403, suffix

    def test_admin_can_decide(
        self,
        client: TestClient,
        login: Login,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        product = factories.make_product(db_session, organization)
        import_rows(client, operator, vendor, [["S-1", "", "x", "1"]])
        admin, _ = login(RoleCode.ADMIN)
        item = the_item(client, admin, "S-1")
        response = client.post(
            f"{EXCEPTIONS}/{item['id']}/approve",
            json={"product_id": str(product.id)},
            headers=admin,
        )
        assert response.status_code == 200

    def test_another_organizations_item_is_not_found(
        self, client: TestClient, manager: tuple[Headers, User], db_session: Session
    ) -> None:
        other = factories.make_organization(db_session)
        foreign_vendor = factories.make_vendor(db_session, other, code="FOREIGN")
        foreign = factories.make_mapping_exception(db_session, other, vendor=foreign_vendor)
        headers, _ = manager
        assert client.get(f"{EXCEPTIONS}/{foreign.id}", headers=headers).status_code == 404
        assert queue(client, headers)["total"] == 0


# --- the queue view -------------------------------------------------------------------------


class TestQueue:
    def test_filters(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = manager
        twin_a = factories.make_product(db_session, organization, name="Twin A")
        twin_b = factories.make_product(db_session, organization, name="Twin B")
        factories.make_identifier(db_session, organization, twin_a, normalized_value=UPC_A)
        factories.make_identifier(
            db_session, organization, twin_b, normalized_value=UPC_A, identifier_type="GTIN"
        )
        other_vendor = factories.make_vendor(db_session, organization, code="OTHER")
        job = import_rows(
            client,
            operator,
            vendor,
            [["AMB-1", "012345678905", "Twin", "1"], ["NONE-1", "", "Mystery", "1"]],
        )
        other_job = import_rows(
            client, operator, other_vendor, [["X-1", "", "Nothing", "1"]], name="o.csv"
        )

        everything = queue(client, headers)
        assert everything["total"] == 3
        # Oldest first in production; here every row shares the test transaction's
        # now(), so only the membership is asserted.
        assert {i["vendor_sku"] for i in everything["items"]} == {"AMB-1", "NONE-1", "X-1"}
        assert all(i["age_hours"] is not None and i["age_hours"] >= 0 for i in everything["items"])
        assert queue(client, headers, reason="AMBIGUOUS_MATCH")["total"] == 1
        assert queue(client, headers, vendor_id=str(other_vendor.id))["total"] == 1
        assert queue(client, headers, import_job_id=job["id"])["total"] == 2
        assert queue(client, headers, import_job_id=other_job["id"])["total"] == 1
        assert queue(client, headers, min_age_hours=1)["total"] == 0
        assert queue(client, headers, status="APPROVED")["total"] == 0
        assert len(queue(client, headers, page=2, page_size=2)["items"]) == 1
        assert client.get(EXCEPTIONS, params={"reason": "nope"}, headers=headers).status_code == 422

    def test_detail_shows_the_source_row_candidates_and_trail(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = manager
        twin_a = factories.make_product(
            db_session, organization, name="Twin A", catalog_item_number="CIN-A"
        )
        twin_b = factories.make_product(
            db_session, organization, name="Twin B", catalog_item_number="CIN-B"
        )
        factories.make_identifier(db_session, organization, twin_a, normalized_value=UPC_A)
        factories.make_identifier(
            db_session, organization, twin_b, normalized_value=UPC_A, identifier_type="GTIN"
        )
        import_rows(client, operator, vendor, [["AMB-1", "012345678905", "Twin", "7"]])
        item = the_item(client, headers, "AMB-1")

        detail = client.get(f"{EXCEPTIONS}/{item['id']}", headers=headers).json()

        assert detail["reason"] == "AMBIGUOUS_MATCH"
        assert detail["source_row"]["raw_data"] == {
            "vendor_sku": "AMB-1",
            "upc": "012345678905",
            "description": "Twin",
            "quantity_available": "7",
        }
        assert detail["source_row"]["normalized_upc"] == UPC_A
        assert detail["source_row"]["quantity"] == "7.000"
        assert sorted(c["product"]["catalog_item_number"] for c in detail["candidates"]) == [
            "CIN-A",
            "CIN-B",
        ]
        assert all(c["rule"] == "UPC" and c["priority"] == 1 for c in detail["candidates"])
        assert detail["match_evaluations"]["source"] == "vendor_import"
        assert detail["match_evaluations"]["rules"][0]["outcome"] == "ambiguous"
        assert detail["vendor_line"]["vendor_sku"] == "AMB-1"
        assert detail["listing"] is None


# --- decisions -------------------------------------------------------------------------------


class TestDecisions:
    def test_reject_records_the_decision_and_makes_no_mapping(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, approver = manager
        product = factories.make_product(db_session, organization, name="Blue Widget")
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        # A UPC match leaves the line PENDING with a SUGGESTION_ONLY item.
        job = import_rows(client, operator, vendor, [["S-1", "012345678905", "x", "1"]])
        item = the_item(client, headers, "S-1")
        assert item["reason"] == "SUGGESTION_ONLY"
        assert line_of(db_session, vendor, "S-1").mapping_status is MappingStatus.PENDING

        empty = client.post(
            f"{EXCEPTIONS}/{item['id']}/reject", json={"note": "  "}, headers=headers
        )
        assert empty.status_code == 422
        response = client.post(
            f"{EXCEPTIONS}/{item['id']}/reject", json={"note": "Wrong pack size."}, headers=headers
        )

        assert response.status_code == 200, response.text
        body = response.json()["exception"]
        assert body["status"] == "REJECTED" and body["resolution_note"] == "Wrong pack size."
        assert (
            body["resolved_by_user_id"] == str(approver.id) and body["resolved_product_id"] is None
        )
        line = line_of(db_session, vendor, "S-1")
        assert line.mapping_status is MappingStatus.UNMAPPED and line.product_id is None
        # The row keeps the chain's verdict; the job's counters are unchanged.
        row = row_of(db_session, job["id"], "S-1")
        assert row.match_result is not None and row.match_result.value == "MATCHED"
        assert queue(client, headers)["total"] == 0
        assert queue(client, headers, status="REJECTED")["total"] == 1
        actions = [e.action for e in audit_rows(db_session, uuid.UUID(item["id"]))]
        assert "mapping_exception.rejected" in actions
        again = client.post(
            f"{EXCEPTIONS}/{item['id']}/reject", json={"note": "x"}, headers=headers
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "exception_not_pending"

    def test_a_rejected_line_is_not_requeued_for_the_same_evidence(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = manager
        import_rows(client, operator, vendor, [["S-1", "", "Mystery", "1"]], name="a.csv")
        item = the_item(client, headers, "S-1")
        client.post(
            f"{EXCEPTIONS}/{item['id']}/reject", json={"note": "Not ours."}, headers=headers
        )

        second = import_rows(client, operator, vendor, [["S-1", "", "Mystery", "2"]], name="b.csv")

        assert queue(client, headers)["total"] == 0
        assert second["exception_rows"] == 1  # the row is still unmatched…
        assert (
            second["error_details"]["matching"]["rejected_not_requeued"] == 1
        )  # …but not re-asked
        row = row_of(db_session, second["id"], "S-1")
        assert row.normalized_data["match"]["previously_rejected"] == item["id"]

        # New evidence (a UPC that now points somewhere) opens a new item.
        product = factories.make_product(db_session, organization, name="Now known")
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        import_rows(
            client, operator, vendor, [["S-1", "012345678905", "Mystery", "3"]], name="c.csv"
        )
        assert queue(client, headers)["total"] == 1

    def test_defer_hides_the_item_until_its_time(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
    ) -> None:
        headers, _ = manager
        import_rows(client, operator, vendor, [["S-1", "", "x", "1"]])
        item = the_item(client, headers, "S-1")
        until = datetime.now(UTC) + timedelta(days=2)

        past = client.post(
            f"{EXCEPTIONS}/{item['id']}/defer",
            json={"until": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
            headers=headers,
        )
        assert past.status_code == 422
        response = client.post(
            f"{EXCEPTIONS}/{item['id']}/defer",
            json={"until": until.isoformat(), "note": "Waiting for the vendor."},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()["exception"]
        assert body["status"] == "PENDING"
        assert datetime.fromisoformat(body["deferred_until"]) == until
        assert queue(client, headers)["total"] == 0
        assert queue(client, headers, include_deferred=True)["total"] == 1
        # Once the time has passed it is back in the default view.
        record = db_session.get(ProductMappingException, uuid.UUID(item["id"]))
        assert record is not None
        record.deferred_until = datetime.now(UTC) - timedelta(minutes=1)
        db_session.flush()
        assert queue(client, headers)["total"] == 1
        assert "mapping_exception.deferred" in [
            e.action for e in audit_rows(db_session, uuid.UUID(item["id"]))
        ]

    def test_approve_refuses_an_unknown_or_inactive_product(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = manager
        inactive = factories.make_product(db_session, organization, is_active=False)
        import_rows(client, operator, vendor, [["S-1", "", "x", "1"]])
        item = the_item(client, headers, "S-1")

        for product_id in (uuid.uuid4(), inactive.id):
            response = client.post(
                f"{EXCEPTIONS}/{item['id']}/approve",
                json={"product_id": str(product_id)},
                headers=headers,
            )
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "product_not_found"
        assert line_of(db_session, vendor, "S-1").mapping_status is MappingStatus.UNMAPPED

    def test_approving_a_pending_suggestion_makes_it_permanent(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, approver = manager
        product = factories.make_product(db_session, organization)
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        import_rows(client, operator, vendor, [["S-1", "012345678905", "x", "1"]])
        item = the_item(client, headers, "S-1")

        response = client.post(
            f"{EXCEPTIONS}/{item['id']}/approve",
            json={"product_id": str(product.id)},
            headers=headers,
        )

        assert response.status_code == 200
        line = line_of(db_session, vendor, "S-1")
        assert line.mapping_status is MappingStatus.APPROVED
        assert line.mapping_approved_by_user_id == approver.id
        actions = [e.action for e in audit_rows(db_session, line.id)]
        assert actions == ["vendor_product.mapping_approved"]


# --- supersession (AC-8.6) -------------------------------------------------------------------


class TestSupersession:
    def test_a_conflicting_approval_needs_the_explicit_flag_and_is_recorded_twice(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, approver = manager
        old_product = factories.make_product(db_session, organization, name="Old")
        new_product = factories.make_product(db_session, organization, name="New")
        factories.make_identifier(db_session, organization, new_product, normalized_value=UPC_A)
        first_approver = factories.make_user(db_session, organization, email="first@x.test")
        line = factories.make_vendor_product(
            db_session,
            organization,
            vendor,
            vendor_sku="S-1",
            normalized_vendor_sku="S-1",
            product_id=old_product.id,
            mapping_status=MappingStatus.APPROVED,
            mapping_approved_by_user_id=first_approver.id,
            mapping_approved_at=datetime.now(UTC) - timedelta(days=30),
        )
        db_session.flush()
        # The chain (UPC, priority 1) disagrees with the approval: a conflict item.
        job = import_rows(client, operator, vendor, [["S-1", "012345678905", "x", "1"]])
        item = the_item(client, headers, "S-1")
        assert item["reason"] == "CONFLICTING_IDENTIFIER"
        assert job["matched_rows"] == 1  # the row carries the chain's answer

        # Without the flag: refused, nothing changes.
        refused = client.post(
            f"{EXCEPTIONS}/{item['id']}/approve",
            json={"product_id": str(new_product.id)},
            headers=headers,
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "mapping_supersession_required"
        assert refused.json()["error"]["details"]["current_product_id"] == str(old_product.id)
        db_session.refresh(line)
        assert line.product_id == old_product.id and line.mapping_status is MappingStatus.APPROVED
        assert line.mapping_approved_by_user_id == first_approver.id

        # Approving the *same* product needs no flag.
        same = client.post(
            f"{EXCEPTIONS}/{item['id']}/approve",
            json={"product_id": str(old_product.id), "supersede": False},
            headers=headers,
        )
        assert same.status_code == 200
        db_session.refresh(line)
        assert line.product_id == old_product.id
        assert line.mapping_approved_by_user_id == approver.id  # re-affirmed by the reviewer

    def test_supersede_true_replaces_the_mapping_with_both_steps_audited(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, approver = manager
        old_product = factories.make_product(db_session, organization, name="Old")
        new_product = factories.make_product(db_session, organization, name="New")
        factories.make_identifier(db_session, organization, new_product, normalized_value=UPC_A)
        line = factories.make_vendor_product(
            db_session,
            organization,
            vendor,
            vendor_sku="S-1",
            normalized_vendor_sku="S-1",
            product_id=old_product.id,
            mapping_status=MappingStatus.APPROVED,
            mapping_approved_at=datetime.now(UTC) - timedelta(days=30),
        )
        db_session.flush()
        import_rows(client, operator, vendor, [["S-1", "012345678905", "x", "1"]])
        item = the_item(client, headers, "S-1")

        response = client.post(
            f"{EXCEPTIONS}/{item['id']}/approve",
            json={"product_id": str(new_product.id), "supersede": True, "note": "Catalog changed."},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["superseded_product_id"] == str(old_product.id)
        db_session.refresh(line)
        assert line.product_id == new_product.id
        assert line.mapping_status is MappingStatus.APPROVED
        assert line.mapping_approved_by_user_id == approver.id
        events = audit_rows(db_session, line.id)
        assert [e.action for e in events] == [
            "vendor_product.mapping_approved",
            "vendor_product.mapping_superseded",
        ]
        superseded = next(e for e in events if e.action == "vendor_product.mapping_superseded")
        assert superseded.before is not None and superseded.after is not None
        assert superseded.before["mapping_status"] == "APPROVED"
        assert superseded.before["product_id"] == str(old_product.id)
        assert superseded.after["mapping_status"] == "SUPERSEDED"
        assert superseded.actor_user_id == approver.id
        approved = next(e for e in events if e.action == "vendor_product.mapping_approved")
        assert approved.before is not None and approved.before["mapping_status"] == "SUPERSEDED"
        assert approved.after is not None and approved.after["product_id"] == str(new_product.id)
        # The next import of the SKU uses the new mapping at priority 3.
        second = import_rows(client, operator, vendor, [["S-1", "", "x", "1"]], name="b.csv")
        row = row_of(db_session, second["id"], "S-1")
        assert row.product_id == new_product.id and row.match_priority == 3
        assert queue(client, headers)["total"] == 0


# --- one exception per row (AC-8.1) -------------------------------------------------------------


def test_every_unresolved_row_produces_exactly_one_item_with_a_reason(
    client: TestClient,
    operator: Headers,
    manager: tuple[Headers, User],
    vendor: Vendor,
    db_session: Session,
    organization: Organization,
) -> None:
    headers, _ = manager
    twin_a = factories.make_product(db_session, organization, name="Twin A")
    twin_b = factories.make_product(db_session, organization, name="Twin B")
    factories.make_identifier(db_session, organization, twin_a, normalized_value=UPC_A)
    factories.make_identifier(
        db_session, organization, twin_b, normalized_value=UPC_A, identifier_type="GTIN"
    )
    factories.make_product(db_session, organization, name="Green Gadget 6 pack")
    job = import_rows(
        client,
        operator,
        vendor,
        [
            ["AMB-1", "012345678905", "Twin", "1"],
            ["SUG-1", "", "Green Gadget 6 pack", "1"],
            ["NONE-1", "", "Mystery", "1"],
        ],
    )
    items = queue(client, headers, import_job_id=job["id"])["items"]
    assert {i["vendor_sku"]: i["reason"] for i in items} == {
        "AMB-1": "AMBIGUOUS_MATCH",
        "SUG-1": "SUGGESTION_ONLY",
        "NONE-1": "NO_MATCH",
    }
    assert len({i["import_job_row_id"] for i in items}) == 3
