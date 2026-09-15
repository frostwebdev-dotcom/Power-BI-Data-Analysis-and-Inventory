"""Profile-driven parsing into import_job_rows and the import report
(AC-5.1, 5.2, 5.5, 5.8; AC-6.4; AC-9.1, 9.2, 9.4, 9.5).

Fixture files are built in-test (``.gitignore`` keeps vendor files out):
the two brief layouts as CSV and XLSX, a latin-1 CSV, a semicolon-delimited
decimal-comma CSV, a file missing a required column, a file with every
per-row problem, and a 20,000-row file for chunking and memory.
"""

from __future__ import annotations

import csv
import io
import tracemalloc
import uuid
from collections.abc import Callable, Iterator
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
from app.imports.profile_rules import compute_header_signature
from app.imports.storage import LocalStorageBackend
from app.main import create_app
from app.models import AuditEvent, Organization, User, Vendor
from app.models.enums import ImportJobStage, ImportJobStatus
from app.models.ingestion import ImportJob, ImportJobRow
from app.services.import_processing import CHUNK_ROWS, process_import_job
from app.services.roles import assign_role, ensure_system_roles
from tests.integration import factories

pytestmark = pytest.mark.integration

IMPORTS = "/api/v1/imports"


# --- fixtures ----------------------------------------------------------------------------


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


# --- the fixture files -------------------------------------------------------------------

BRIEF_A = ["UPC", "Description", "Quantity", "Cost"]
BRIEF_B = ["Vendor SKU", "UPC", "Qty Available", "Wholesale Price"]

BRIEF_A_ROWS: list[list[Any]] = [
    ["012345678905", "Blue Widget", "24", "$3.50"],
    ["036000291452", "Red Widget", "0", "4.00"],
    ["012345678905", "Blue Widget again", "5", "3.25"],  # a UPC repeat is not a SKU repeat
]
BRIEF_B_ROWS: list[list[Any]] = [
    ["ACM-001", 12345678905, 100, 12.5],  # numeric UPC that lost its leading zero (AC-5.5)
    ["ACM-002", "036000291452", 0, 7],
    ["ACM-003", None, 3, 0.99],  # SKU only
]


def csv_bytes(
    headers: list[str], rows: list[list[Any]], *, delimiter: str = ",", encoding: str = "utf-8"
) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return buffer.getvalue().encode(encoding)


def xlsx_bytes(headers: list[str], rows: list[list[Any]], *, sheet: str = "Sheet1") -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = sheet
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def profile_a(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "brief-a",
        "file_format": "CSV",
        "column_map": {
            "columns": [
                {"target": "upc", "source": "UPC"},
                {"target": "description", "source": "Description"},
                {"target": "quantity_available", "source": "Quantity"},
                {"target": "unit_cost", "source": "Cost"},
            ]
        },
        "header_signature": compute_header_signature(BRIEF_A),
    }
    body.update(overrides)
    return body


def profile_b(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "brief-b",
        "file_format": "XLSX",
        "column_map": {
            "columns": [
                {"target": "vendor_sku", "source": "Vendor SKU", "required": True},
                {"target": "upc", "source": "UPC"},
                {"target": "quantity_available", "source": "Qty Available"},
                {"target": "unit_cost", "source": "Wholesale Price"},
            ]
        },
        "header_signature": compute_header_signature(BRIEF_B),
    }
    body.update(overrides)
    return body


# --- helpers -------------------------------------------------------------------------------


def create_profile(
    client: TestClient, headers: Headers, vendor: Vendor, body: dict[str, Any]
) -> str:
    response = client.post(
        f"/api/v1/vendors/{vendor.id}/import-profiles", json=body, headers=headers
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def upload(
    client: TestClient,
    headers: Headers,
    vendor: Vendor,
    profile_id: str | None,
    name: str,
    content: bytes,
) -> Any:
    data = {"vendor_id": str(vendor.id)}
    if profile_id:
        data["profile_id"] = profile_id
    response = client.post(IMPORTS, data=data, files={"file": (name, content)}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["job"]


def import_file(
    client: TestClient,
    headers: Headers,
    vendor: Vendor,
    profile: dict[str, Any],
    name: str,
    content: bytes,
) -> Any:
    """Create the profile, upload with it; parsing runs inside the upload."""
    profile_id = create_profile(client, headers, vendor, profile)
    return upload(client, headers, vendor, profile_id, name, content)


def report(client: TestClient, headers: Headers, job_id: str) -> Any:
    response = client.get(f"{IMPORTS}/{job_id}/report", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def rows(client: TestClient, headers: Headers, job_id: str, **params: Any) -> list[Any]:
    response = client.get(
        f"{IMPORTS}/{job_id}/rows", params={"page_size": 200, **params}, headers=headers
    )
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def audit_actions(db_session: Session, job_id: str) -> list[str]:
    return sorted(
        db_session.execute(
            select(AuditEvent.action).where(
                AuditEvent.entity_id == uuid.UUID(job_id), AuditEvent.action.like("import_job.%")
            )
        )
        .scalars()
        .all()
    )


# --- the brief's layouts -------------------------------------------------------------------


class TestBriefLayouts:
    def test_layout_a_as_csv(
        self, client: TestClient, operator: Headers, vendor: Vendor, db_session: Session
    ) -> None:
        job = import_file(
            client, operator, vendor, profile_a(), "a.csv", csv_bytes(BRIEF_A, BRIEF_A_ROWS)
        )

        assert job["status"] == "RUNNING" and job["current_stage"] == "MATCHING"
        assert job["total_rows"] == 3 and job["processed_rows"] == 3
        assert job["error_rows"] == 0 and job["skipped_rows"] == 0
        assert job["profile_version"] == 1

        staged = rows(client, operator, job["id"])
        assert [r["row_number"] for r in staged] == [2, 3, 4]  # the file's own line numbers
        first = staged[0]
        assert first["status"] == "OK" and first["error_code"] is None
        assert first["raw_data"] == {
            "upc": "012345678905",
            "description": "Blue Widget",
            "quantity_available": "24",
            "unit_cost": "$3.50",
        }
        assert first["raw_upc"] == "012345678905" and first["normalized_upc"] == "00012345678905"
        assert first["quantity"] == "24.000" and first["unit_cost"] == "3.5000"
        assert first["currency"] == "USD" and first["availability_status"] == "AVAILABLE"
        assert first["normalized_data"]["upc_valid_checksum"] is True
        assert first["normalized_data"]["issues"] == []
        assert staged[1]["availability_status"] == "OUT_OF_STOCK"
        assert staged[2]["status"] == "OK"  # two rows with one UPC and no SKU are not duplicates

        body = report(client, operator, job["id"])
        assert body["counters"] == {
            "total": 3,
            "processed": 3,
            "ok": 3,
            "warning": 0,
            "error": 0,
            "skipped": 0,
            "matched": 0,
            "exception": 0,
        }
        assert body["by_error_code"] == {} and body["issues_by_code"] == {}
        assert body["summary"] == "3 rows imported. Import is still running (matching)."
        assert audit_actions(db_session, job["id"]) == [
            "import_job.created",
            "import_job.parsed",
            "import_job.started",
        ]

    def test_layout_b_as_xlsx(self, client: TestClient, operator: Headers, vendor: Vendor) -> None:
        job = import_file(
            client, operator, vendor, profile_b(), "b.xlsx", xlsx_bytes(BRIEF_B, BRIEF_B_ROWS)
        )

        assert job["status"] == "RUNNING" and job["total_rows"] == 3
        staged = rows(client, operator, job["id"])
        assert [r["row_number"] for r in staged] == [2, 3, 4]  # sheet rows
        assert [r["vendor_sku"] for r in staged] == ["ACM-001", "ACM-002", "ACM-003"]
        # The numeric UPC cell is read as digits and its leading zero restored (AC-5.5).
        assert staged[0]["raw_upc"] == "12345678905"
        assert staged[0]["normalized_upc"] == "00012345678905"
        assert staged[0]["quantity"] == "100.000" and staged[0]["unit_cost"] == "12.5000"
        assert staged[2]["raw_upc"] is None and staged[2]["status"] == "OK"  # SKU alone is fine
        assert staged[2]["unit_cost"] == "0.9900"
        assert report(client, operator, job["id"])["counters"]["ok"] == 3

    def test_layout_a_as_xlsx_and_b_as_csv(
        self, client: TestClient, operator: Headers, vendor: Vendor
    ) -> None:
        a = import_file(
            client,
            operator,
            vendor,
            profile_a(file_format="XLSX", name="a-xlsx"),
            "a.xlsx",
            xlsx_bytes(BRIEF_A, [[12345678905, "Widget", 24, 3.5]]),
        )
        b = import_file(
            client,
            operator,
            vendor,
            profile_b(file_format="CSV", name="b-csv"),
            "b.csv",
            csv_bytes(BRIEF_B, BRIEF_B_ROWS),
        )
        assert rows(client, operator, a["id"])[0]["normalized_upc"] == "00012345678905"
        assert [r["status"] for r in rows(client, operator, b["id"])] == ["OK", "OK", "OK"]


# --- encodings and delimiters (AC-5.8) ---------------------------------------------------


class TestEncodingsAndDelimiters:
    def test_latin_1_csv(self, client: TestClient, operator: Headers, vendor: Vendor) -> None:
        content = csv_bytes(
            BRIEF_A, [["012345678905", "Café crème Ñandú", "2", "1.00"]], encoding="latin-1"
        )

        job = import_file(client, operator, vendor, profile_a(), "latin.csv", content)

        assert job["status"] == "RUNNING"
        assert job["import_file"]["detected_encoding"] == "cp1252"
        assert job["error_details"]["parsing"]["encoding"] == "cp1252"
        [row] = rows(client, operator, job["id"])
        assert row["description"] == "Café crème Ñandú"

    def test_declared_encoding_is_used_strictly(
        self, client: TestClient, operator: Headers, vendor: Vendor
    ) -> None:
        content = csv_bytes(BRIEF_A, [["012345678905", "Ñ", "2", "1"]], encoding="latin-1")

        job = import_file(client, operator, vendor, profile_a(encoding="utf-8"), "x.csv", content)

        assert job["status"] == "FAILED"
        assert job["error_details"]["code"] == "FILE_UNREADABLE"
        assert "utf-8" in job["error_details"]["reason"]

    def test_semicolon_delimiter_and_decimal_commas(
        self, client: TestClient, operator: Headers, vendor: Vendor
    ) -> None:
        content = csv_bytes(
            BRIEF_A,
            [
                ["012345678905", "Widget", "1.250", "€ 1.299,50"],
                ["036000291452", "Gadget", "3", "0,99"],
            ],
            delimiter=";",
        )
        profile = profile_a(
            delimiter=";",
            normalization_rules={
                "decimal_separator": ",",
                "thousands_separator": ".",
                "currency_symbols_to_strip": ["€"],
            },
        )

        job = import_file(client, operator, vendor, profile, "eu.csv", content)

        assert job["status"] == "RUNNING", job["error_details"]
        staged = rows(client, operator, job["id"])
        assert staged[0]["quantity"] == "1250.000" and staged[0]["unit_cost"] == "1299.5000"
        assert staged[1]["unit_cost"] == "0.9900"
        assert report(client, operator, job["id"])["counters"]["ok"] == 2


# --- file-level failures (AC-6.4, AC-9.2) -----------------------------------------------


class TestFileLevelFailures:
    def test_missing_required_column_fails_before_any_row(
        self, client: TestClient, operator: Headers, vendor: Vendor, db_session: Session
    ) -> None:
        content = csv_bytes(["UPC", "Description", "Cost"], [["012345678905", "x", "1"]])

        job = import_file(
            client, operator, vendor, profile_a(header_signature=None), "short.csv", content
        )

        assert job["status"] == "FAILED" and job["current_stage"] is None
        assert job["error_details"]["code"] == "REQUIRED_COLUMN_MISSING"
        assert job["error_details"]["missing_columns"] == [
            {"target": "quantity_available", "source": "Quantity"}
        ]
        assert job["error_details"]["observed_headers"] == ["UPC", "Description", "Cost"]
        assert "quantity_available" in job["error_message"]
        assert job["total_rows"] == 0
        assert db_session.execute(select(ImportJobRow)).scalars().all() == []
        assert report(client, operator, job["id"])["summary"].startswith("Import failed: ")
        assert audit_actions(db_session, job["id"]) == [
            "import_job.created",
            "import_job.failed",
            "import_job.started",
        ]

    def test_header_signature_mismatch_fails_with_the_diff(
        self, client: TestClient, operator: Headers, vendor: Vendor, db_session: Session
    ) -> None:
        # Every mapped column is present, but a column was added: positional
        # mapping would still "work", which is exactly what AC-6.4 forbids.
        content = csv_bytes([*BRIEF_A, "MAP"], [["012345678905", "x", "1", "1", "9"]])

        job = import_file(client, operator, vendor, profile_a(), "drift.csv", content)

        assert job["status"] == "FAILED"
        details = job["error_details"]
        assert details["code"] == "HEADER_SIGNATURE_MISMATCH"
        assert details["expected_signature"] == compute_header_signature(BRIEF_A)
        assert details["header_signature"] == compute_header_signature([*BRIEF_A, "MAP"])
        assert details["observed_headers"] == [*BRIEF_A, "MAP"]
        assert [c["source"] for c in details["expected_columns"]] == BRIEF_A
        assert db_session.execute(select(ImportJobRow)).scalars().all() == []

    def test_an_unpinned_profile_accepts_any_matching_header(
        self, client: TestClient, operator: Headers, vendor: Vendor
    ) -> None:
        content = csv_bytes([*BRIEF_A, "MAP"], [["012345678905", "x", "1", "1", "9"]])
        job = import_file(
            client, operator, vendor, profile_a(header_signature=None), "x.csv", content
        )
        assert job["status"] == "RUNNING" and job["total_rows"] == 1

    def test_a_job_without_a_profile_stays_pending_then_fails_when_run(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        job = upload(client, operator, vendor, None, "a.csv", csv_bytes(BRIEF_A, BRIEF_A_ROWS))
        assert job["status"] == "PENDING"

        processed = process_import_job(
            db_session,
            LocalStorageBackend(raw_dir),
            organization_id=organization.id,
            job_id=uuid.UUID(job["id"]),
        )

        assert processed.status is ImportJobStatus.FAILED
        assert processed.error_details["code"] == "PROFILE_MISSING"

    def test_only_a_pending_job_can_be_started(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        from app.services.import_processing import ImportJobNotPending

        job = import_file(
            client, operator, vendor, profile_a(), "a.csv", csv_bytes(BRIEF_A, BRIEF_A_ROWS)
        )
        with pytest.raises(ImportJobNotPending):
            process_import_job(
                db_session,
                LocalStorageBackend(raw_dir),
                organization_id=organization.id,
                job_id=uuid.UUID(job["id"]),
            )

    def test_an_unreadable_workbook_fails_the_job(
        self, client: TestClient, operator: Headers, vendor: Vendor
    ) -> None:
        job = import_file(
            client, operator, vendor, profile_b(), "bad.xlsx", b"PK\x03\x04 not really"
        )
        assert job["status"] == "FAILED"
        assert job["error_details"]["code"] == "FILE_UNREADABLE"

    def test_a_tampered_retained_file_fails_the_job(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        profile_id = create_profile(client, operator, vendor, profile_a())
        job = upload(client, operator, vendor, None, "a.csv", csv_bytes(BRIEF_A, BRIEF_A_ROWS))
        record = db_session.get(ImportJob, uuid.UUID(job["id"]))
        assert record is not None
        record.vendor_import_profile_id = uuid.UUID(profile_id)
        db_session.flush()
        [stored] = list(raw_dir.rglob("*.csv"))
        stored.write_bytes(b"UPC,Description,Quantity,Cost\r\nchanged\r\n")

        processed = process_import_job(
            db_session,
            LocalStorageBackend(raw_dir),
            organization_id=organization.id,
            job_id=record.id,
        )

        assert processed.status is ImportJobStatus.FAILED
        assert processed.error_details["code"] == "FILE_INTEGRITY"


# --- every per-row problem (AC-9.1, AC-9.4) ----------------------------------------------

PROBLEMS_HEADERS = ["SKU", "UPC", "Desc", "Qty", "Price", "Status"]
PROBLEMS_ROWS: list[list[Any]] = [
    # line 2: clean
    ["A-1", "012345678905", "Clean", "10", "1.50", "In Stock"],
    # line 3: invalid UPC (bad check digit) → WARNING, imports on the SKU
    ["A-2", "012345678906", "Bad UPC", "5", "2.00", "In Stock"],
    # line 4: non-numeric quantity → ERROR
    ["A-3", "036000291452", "Qty text", "lots", "2.00", "In Stock"],
    # line 5: blank quantity → ERROR
    ["A-4", "036000291452", "Qty blank", "", "2.00", "In Stock"],
    # line 6: negative quantity → ERROR
    ["A-5", "036000291452", "Qty negative", "-4", "2.00", "In Stock"],
    # line 7: non-numeric price → WARNING, imports with null cost
    ["A-6", "036000291452", "Price text", "3", "call", "In Stock"],
    # line 8: empty row → SKIPPED
    ["", "", "", "", "", ""],
    # line 9: first occurrence of a duplicate SKU → WARNING DUPLICATE_IN_FILE
    ["A-7", "036000291452", "Dup first", "1", "1.00", "In Stock"],
    # line 10: no identifier at all → ERROR
    ["", "", "Nothing", "1", "1.00", "In Stock"],
    # line 11: unrecognised status → WARNING, availability UNKNOWN
    ["A-8", "036000291452", "Odd status", "2", "1.00", "Backorder"],
    # line 12: last occurrence of the duplicate SKU (case-insensitive) → OK, this one counts
    ["a-7", "036000291452", "Dup last", "9", "1.10", "Discontinued"],
    # line 13: invalid UPC *and* invalid quantity → ERROR, both issues recorded
    ["A-9", "123", "Two problems", "x", "1.00", "In Stock"],
]


def problems_profile() -> dict[str, Any]:
    return {
        "name": "problems",
        "file_format": "CSV",
        "column_map": {
            "columns": [
                {"target": "vendor_sku", "source": "SKU"},
                {"target": "upc", "source": "UPC"},
                {"target": "description", "source": "Desc"},
                {"target": "quantity_available", "source": "Qty"},
                {"target": "unit_cost", "source": "Price"},
            ]
        },
        "availability_rules": {
            "available_when": "status_column",
            "status_column": "Status",
            "available_values": ["In Stock"],
            "unavailable_values": ["Discontinued"],
        },
    }


class TestEveryRowProblem:
    def test_counters_statuses_and_codes_are_exact(
        self, client: TestClient, operator: Headers, vendor: Vendor
    ) -> None:
        job = import_file(
            client,
            operator,
            vendor,
            problems_profile(),
            "p.csv",
            csv_bytes(PROBLEMS_HEADERS, PROBLEMS_ROWS),
        )

        assert job["status"] == "RUNNING", job["error_details"]
        assert job["total_rows"] == 12
        assert job["processed_rows"] == 11 and job["skipped_rows"] == 1
        assert job["error_rows"] == 5

        staged = {r["row_number"]: r for r in rows(client, operator, job["id"])}
        assert sorted(staged) == list(range(2, 14))
        expected = {
            2: ("OK", None),
            3: ("WARNING", "UPC_INVALID"),
            4: ("ERROR", "QUANTITY_INVALID"),
            5: ("ERROR", "QUANTITY_INVALID"),
            6: ("ERROR", "QUANTITY_NEGATIVE"),
            7: ("WARNING", "PRICE_INVALID"),
            8: ("SKIPPED", None),
            9: ("WARNING", "DUPLICATE_IN_FILE"),
            10: ("ERROR", "IDENTIFIER_MISSING"),
            11: ("WARNING", "AVAILABILITY_UNKNOWN"),
            12: ("OK", None),
            13: ("ERROR", "QUANTITY_INVALID"),
        }
        assert {n: (r["status"], r["error_code"]) for n, r in staged.items()} == expected

        # The bad-UPC row imports on its SKU; the bad-price row with a null cost.
        assert staged[3]["vendor_sku"] == "A-2" and staged[3]["normalized_upc"] == "00012345678906"
        assert staged[3]["normalized_data"]["upc_valid_checksum"] is False
        assert staged[7]["unit_cost"] is None and staged[7]["quantity"] == "3.000"
        # The superseded duplicate names the row that replaced it; the last one is OK.
        assert staged[9]["normalized_data"]["superseded_by_row"] == 12
        assert [i["code"] for i in staged[9]["normalized_data"]["issues"]] == ["DUPLICATE_IN_FILE"]
        assert staged[12]["normalized_vendor_sku"] == "A-7" and staged[12]["quantity"] == "9.000"
        assert staged[12]["availability_status"] == "OUT_OF_STOCK"
        assert staged[11]["availability_status"] == "UNKNOWN"
        # Both issues on the two-problem row, the ERROR one decides.
        assert [i["code"] for i in staged[13]["normalized_data"]["issues"]] == [
            "UPC_INVALID",
            "QUANTITY_INVALID",
        ]
        assert [i["severity"] for i in staged[13]["normalized_data"]["issues"]] == [
            "WARNING",
            "ERROR",
        ]
        assert staged[8]["raw_data"] == {} and staged[8]["error_message"] == "the row is empty"

        body = report(client, operator, job["id"])
        assert body["counters"] == {
            "total": 12,
            "processed": 11,
            "ok": 2,
            "warning": 4,
            "error": 5,
            "skipped": 1,
            "matched": 0,
            "exception": 0,
        }
        # ok + warning + error + skipped == total (AC-9.4)
        c = body["counters"]
        assert c["ok"] + c["warning"] + c["error"] + c["skipped"] == c["total"]
        assert body["by_error_code"] == {
            "UPC_INVALID": 1,
            "QUANTITY_INVALID": 3,
            "QUANTITY_NEGATIVE": 1,
            "PRICE_INVALID": 1,
            "DUPLICATE_IN_FILE": 1,
            "IDENTIFIER_MISSING": 1,
            "AVAILABILITY_UNKNOWN": 1,
        }
        assert body["issues_by_code"] == {
            "AVAILABILITY_UNKNOWN": 1,
            "DUPLICATE_IN_FILE": 1,
            "IDENTIFIER_MISSING": 1,
            "PRICE_INVALID": 1,
            "QUANTITY_INVALID": 3,
            "QUANTITY_NEGATIVE": 1,
            "UPC_INVALID": 2,
        }
        assert body["summary"] == (
            "6 rows imported, 5 rejected, 3 with invalid quantities, 1 with negative quantities, "
            "2 with invalid UPCs, 1 with invalid prices, 1 without a UPC or vendor SKU, "
            "1 with unrecognised availability, 1 superseded by a later duplicate vendor SKU, "
            "1 empty rows skipped. Import is still running (matching)."
        )

    def test_rows_can_be_filtered_by_status_and_code(
        self, client: TestClient, operator: Headers, vendor: Vendor
    ) -> None:
        job = import_file(
            client,
            operator,
            vendor,
            problems_profile(),
            "p.csv",
            csv_bytes(PROBLEMS_HEADERS, PROBLEMS_ROWS),
        )

        errors = rows(client, operator, job["id"], status="ERROR")
        assert [r["row_number"] for r in errors] == [4, 5, 6, 10, 13]
        warnings = rows(client, operator, job["id"], status="WARNING")
        assert [r["row_number"] for r in warnings] == [3, 7, 9, 11]
        assert [
            r["row_number"]
            for r in rows(client, operator, job["id"], error_code="QUANTITY_INVALID")
        ] == [4, 5, 13]
        assert [r["row_number"] for r in rows(client, operator, job["id"], status="SKIPPED")] == [8]
        paged = client.get(
            f"{IMPORTS}/{job['id']}/rows", params={"page": 2, "page_size": 5}, headers=operator
        ).json()
        assert paged["total"] == 12 and [r["row_number"] for r in paged["items"]] == [
            7,
            8,
            9,
            10,
            11,
        ]
        assert (
            client.get(
                f"{IMPORTS}/{job['id']}/rows", params={"status": "nope"}, headers=operator
            ).status_code
            == 422
        )

    def test_report_and_rows_are_readable_by_a_viewer_and_scoped(
        self,
        client: TestClient,
        login: Login,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
    ) -> None:
        job = import_file(
            client, operator, vendor, profile_a(), "a.csv", csv_bytes(BRIEF_A, BRIEF_A_ROWS)
        )
        viewer, _ = login(RoleCode.VIEWER)
        assert client.get(f"{IMPORTS}/{job['id']}/report", headers=viewer).status_code == 200
        assert client.get(f"{IMPORTS}/{job['id']}/rows", headers=viewer).status_code == 200
        assert client.get(f"{IMPORTS}/{uuid.uuid4()}/report", headers=viewer).status_code == 404
        assert client.get(f"{IMPORTS}/{job['id']}/report").status_code == 401


# --- chunking and memory (20,000 rows) ----------------------------------------------------


def big_rows(count: int) -> Iterator[list[Any]]:
    for i in range(count):
        # Every 500th row is blank, every 1000th has a bad quantity: the
        # counters must come out exact across chunk boundaries.
        if i % 500 == 499:
            yield ["", "", "", ""]
        elif i % 1000 == 998:
            yield [f"SKU-{i:06d}", "036000291452", "lots", "1.00"]
        else:
            yield [f"SKU-{i:06d}", "036000291452", str(i % 7), "1.00"]


BIG_HEADERS = ["SKU", "UPC", "Qty", "Cost"]
BIG_ROWS = 20_000
BIG_BLANK = BIG_ROWS // 500  # 40
BIG_BAD = BIG_ROWS // 1000  # 20


def big_profile(file_format: str) -> dict[str, Any]:
    return {
        "name": f"big-{file_format.lower()}",
        "file_format": file_format,
        "column_map": {
            "columns": [
                {"target": "vendor_sku", "source": "SKU"},
                {"target": "upc", "source": "UPC"},
                {"target": "quantity_available", "source": "Qty"},
                {"target": "unit_cost", "source": "Cost"},
            ]
        },
    }


class TestTwentyThousandRows:
    @pytest.mark.parametrize("file_format", ["CSV", "XLSX"])
    def test_chunked_import_is_exact_and_memory_stays_flat(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
        file_format: str,
    ) -> None:
        if file_format == "CSV":
            content = csv_bytes(BIG_HEADERS, list(big_rows(BIG_ROWS)))
        else:
            content = xlsx_bytes(BIG_HEADERS, list(big_rows(BIG_ROWS)))
        profile_id = create_profile(client, operator, vendor, big_profile(file_format))
        # Upload without processing, then run the parser directly under tracemalloc.
        job = upload(client, operator, vendor, None, f"big.{file_format.lower()}", content)
        record = db_session.get(ImportJob, uuid.UUID(job["id"]))
        assert record is not None
        record.vendor_import_profile_id = uuid.UUID(profile_id)
        db_session.flush()

        tracemalloc.start()
        try:
            processed = process_import_job(
                db_session,
                LocalStorageBackend(raw_dir),
                organization_id=organization.id,
                job_id=record.id,
            )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert processed.status is ImportJobStatus.RUNNING, processed.error_details
        assert processed.current_stage is ImportJobStage.MATCHING
        assert processed.total_rows == BIG_ROWS
        assert processed.skipped_rows == BIG_BLANK
        assert processed.error_rows == BIG_BAD
        assert processed.processed_rows == BIG_ROWS - BIG_BLANK
        assert processed.error_details["parsing"]["chunks"] == BIG_ROWS // CHUNK_ROWS
        assert processed.error_details["parsing"]["by_status"] == {
            "PENDING": 0,
            "OK": BIG_ROWS - BIG_BLANK - BIG_BAD,
            "WARNING": 0,
            "ERROR": BIG_BAD,
            "SKIPPED": BIG_BLANK,
        }
        count = (
            db_session.execute(
                select(ImportJobRow.row_number).where(ImportJobRow.import_job_id == record.id)
            )
            .scalars()
            .all()
        )
        assert len(count) == BIG_ROWS and len(set(count)) == BIG_ROWS
        assert max(count) == BIG_ROWS + 1  # header is row 1
        # Flat memory: one chunk plus the SKU index, not the file's rows.
        budget = 48 * 1024 * 1024
        assert peak < budget, f"peak traced memory {peak / 1024 / 1024:.1f} MB"
        # The whole file in memory as one list of rows would be several times that.
        body = report(client, operator, job["id"])
        assert body["counters"]["total"] == BIG_ROWS and body["counters"]["error"] == BIG_BAD
        assert body["summary"].startswith(
            f"{BIG_ROWS - BIG_BLANK - BIG_BAD:,} rows imported, {BIG_BAD} rejected"
        )
