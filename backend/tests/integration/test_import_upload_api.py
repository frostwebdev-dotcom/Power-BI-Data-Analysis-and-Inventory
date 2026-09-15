"""File upload with byte-identical raw retention, end to end (AC-5.3, 5.4,
5.6, 5.8; ADR 0004).

The proof that matters is a hash equality: SHA-256 of the bytes sent ==
SHA-256 recorded on ``import_files`` == SHA-256 of the object on disk ==
SHA-256 of what ``GET /imports/{id}/raw`` returns.
"""

from __future__ import annotations

import hashlib
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
from app.main import create_app
from app.models import AuditEvent, Organization, User, Vendor
from app.models.enums import ImportJobStatus
from app.models.ingestion import ImportFile, ImportJob
from app.services.roles import assign_role, ensure_system_roles
from tests.integration import factories

pytestmark = pytest.mark.integration

IMPORTS = "/api/v1/imports"
MAX_MB = 1  # small so the size test does not have to build 50 MB


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
        import_max_upload_mb=MAX_MB,
        # These tests are about receiving and retaining; parsing has its own
        # suite (test_import_processing.py).
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


CSV_BYTES = b"\xef\xbb\xbfUPC,Description,Quantity,Cost\r\n012345678905,Widget,7,3.50\r\n"


def xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Vendor SKU", "UPC", "Qty Available", "Wholesale Price"])
    sheet.append(["ACM-1", 12345678905, 3, 1.25])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def upload(
    client: TestClient,
    headers: Headers,
    vendor: Vendor,
    *,
    name: str = "prices.csv",
    content: bytes = CSV_BYTES,
    profile_id: uuid.UUID | None = None,
    mime: str = "text/csv",
) -> Any:
    data: dict[str, str] = {"vendor_id": str(vendor.id)}
    if profile_id is not None:
        data["profile_id"] = str(profile_id)
    return client.post(IMPORTS, data=data, files={"file": (name, content, mime)}, headers=headers)


def set_status(db_session: Session, job_id: str, status: ImportJobStatus) -> None:
    """Move a job to ``status`` the way the schema requires: a terminal status
    carries ``started_at`` and ``completed_at``."""
    job = db_session.get(ImportJob, uuid.UUID(job_id))
    assert job is not None
    job.status = status
    if status is not ImportJobStatus.PENDING:
        job.started_at = datetime.now(UTC)
    if status not in (ImportJobStatus.PENDING, ImportJobStatus.RUNNING):
        job.completed_at = datetime.now(UTC)
    db_session.flush()


def audit_rows(db_session: Session, entity_id: uuid.UUID) -> list[AuditEvent]:
    return sorted(
        db_session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == entity_id, AuditEvent.action.like("import_%")
            )
        )
        .scalars()
        .all(),
        key=lambda e: e.action,
    )


# --- access control -----------------------------------------------------------------------


class TestAccessControl:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", IMPORTS),
            ("GET", IMPORTS),
            ("GET", f"{IMPORTS}/{uuid.uuid4()}"),
            ("GET", f"{IMPORTS}/{uuid.uuid4()}/raw"),
        ],
    )
    def test_every_route_requires_authentication(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, path)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    @pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.PURCHASING_MANAGER])
    def test_read_roles_cannot_upload(
        self, client: TestClient, login: Login, vendor: Vendor, role: RoleCode, raw_dir: Path
    ) -> None:
        headers, _ = login(role)

        response = upload(client, headers, vendor)

        assert response.status_code == 403
        assert not raw_dir.exists()

    def test_viewer_can_list_inspect_and_download(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        operator, _ = login(RoleCode.DATA_OPERATOR)
        job = upload(client, operator, vendor).json()["job"]
        viewer, _ = login(RoleCode.VIEWER)

        assert client.get(IMPORTS, headers=viewer).json()["total"] == 1
        assert client.get(f"{IMPORTS}/{job['id']}", headers=viewer).status_code == 200
        raw = client.get(f"{IMPORTS}/{job['id']}/raw", headers=viewer)
        assert raw.status_code == 200 and raw.content == CSV_BYTES

    def test_another_organizations_import_is_not_found(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        other = factories.make_organization(db_session)
        foreign_vendor = factories.make_vendor(db_session, other, code="FOREIGN")
        foreign_file = factories.make_import_file(db_session, other, foreign_vendor)
        foreign_job = factories.make_import_job(db_session, other, foreign_vendor, foreign_file)
        headers, _ = login(RoleCode.DATA_OPERATOR)

        assert client.get(f"{IMPORTS}/{foreign_job.id}", headers=headers).status_code == 404
        assert client.get(f"{IMPORTS}/{foreign_job.id}/raw", headers=headers).status_code == 404
        assert client.get(IMPORTS, headers=headers).json()["total"] == 0
        assert upload(client, headers, foreign_vendor).status_code == 404


# --- retention ------------------------------------------------------------------------------


class TestRetention:
    def test_csv_round_trip_is_byte_identical(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        organization: Organization,
    ) -> None:
        headers, user = login(RoleCode.DATA_OPERATOR)
        expected = sha(CSV_BYTES)

        response = upload(client, headers, vendor, name="Weekly Prices.csv")

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["duplicate"] is False
        job = body["job"]
        file = job["import_file"]
        assert job["status"] == "PENDING"
        assert job["vendor_id"] == str(vendor.id)
        assert job["triggered_by_user_id"] == str(user.id)
        assert job["vendor_import_profile_id"] is None and job["profile_version"] is None

        # 1. what was recorded
        assert file["sha256"] == expected
        assert file["size_bytes"] == len(CSV_BYTES)
        assert file["original_filename"] == "Weekly Prices.csv"
        assert file["declared_mime"] == "text/csv"
        assert file["detected_encoding"] == "utf-8-sig"
        assert file["uploaded_by_user_id"] == str(user.id)
        assert file["storage_uri"].startswith(f"local://{organization.id}/")

        # 2. what is on disk, under <org>/<yyyy>/<mm>/<sha256>.csv
        [stored] = list(raw_dir.rglob("*.csv"))
        assert stored.name == f"{expected}.csv"
        relative = stored.relative_to(raw_dir).parts
        assert relative[0] == str(organization.id)
        assert len(relative) == 4 and relative[1].isdigit() and relative[2].isdigit()
        assert sha(stored.read_bytes()) == expected
        assert stored.read_bytes() == CSV_BYTES  # BOM and CRLF intact

        # 3. what comes back out
        raw = client.get(f"{IMPORTS}/{job['id']}/raw", headers=headers)
        assert raw.status_code == 200
        assert sha(raw.content) == expected
        assert raw.headers["content-type"].startswith("text/csv")
        assert raw.headers["x-content-sha256"] == expected
        assert 'filename="Weekly Prices.csv"' in raw.headers["content-disposition"]

    def test_xlsx_round_trip_is_byte_identical(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        content = xlsx_bytes()

        response = upload(
            client,
            headers,
            vendor,
            name="wholesale.xlsx",
            content=content,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        assert response.status_code == 201, response.text
        file = response.json()["job"]["import_file"]
        assert file["sha256"] == sha(content)
        assert file["detected_encoding"] is None  # not a text file
        raw = client.get(f"{IMPORTS}/{response.json()['job']['id']}/raw", headers=headers)
        assert raw.content == content
        assert raw.headers["content-type"].startswith("application/vnd.openxmlformats")

    def test_cp1252_encoding_is_recorded(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        content = "UPC,Description\r\n1,Caf\xe9\r\n".encode("cp1252")
        headers, _ = login(RoleCode.DATA_OPERATOR)

        file = upload(client, headers, vendor, content=content).json()["job"]["import_file"]

        assert file["detected_encoding"] == "cp1252"

    def test_a_non_ascii_filename_survives(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        job = upload(client, headers, vendor, name="Preisliste März.csv").json()["job"]

        assert job["import_file"]["original_filename"] == "Preisliste März.csv"
        raw = client.get(f"{IMPORTS}/{job['id']}/raw", headers=headers)
        assert "filename*=UTF-8''Preisliste%20M%C3%A4rz.csv" in raw.headers["content-disposition"]

    def test_audit_rows_for_file_and_job(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session
    ) -> None:
        headers, user = login(RoleCode.DATA_OPERATOR)

        job = upload(client, headers, vendor).json()["job"]

        [received] = audit_rows(db_session, uuid.UUID(job["import_file"]["id"]))
        assert received.action == "import_file.received"
        assert received.entity_type == "import_files"
        assert received.actor_user_id == user.id
        assert received.before is None
        assert received.after is not None and received.after["sha256"] == sha(CSV_BYTES)
        [created] = audit_rows(db_session, uuid.UUID(job["id"]))
        assert created.action == "import_job.created"
        assert created.entity_type == "import_jobs"
        assert created.after is not None and created.after["status"] == "PENDING"


# --- duplicates (AC-5.6) --------------------------------------------------------------------


class TestDuplicates:
    def test_identical_bytes_return_the_existing_job(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session, raw_dir: Path
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        first = upload(client, headers, vendor, name="monday.csv")

        second = upload(client, headers, vendor, name="tuesday.csv")

        assert second.status_code == 200, second.text
        assert second.json()["duplicate"] is True
        assert second.json()["job"]["id"] == first.json()["job"]["id"]
        assert second.json()["job"]["import_file"]["original_filename"] == "monday.csv"
        assert len(db_session.execute(select(ImportFile)).scalars().all()) == 1
        assert len(db_session.execute(select(ImportJob)).scalars().all()) == 1
        assert len(list(raw_dir.rglob("*.csv"))) == 1
        # No audit row for the no-op.
        assert len(audit_rows(db_session, uuid.UUID(first.json()["job"]["id"]))) == 1

    @pytest.mark.parametrize("status", [ImportJobStatus.FAILED, ImportJobStatus.CANCELLED])
    def test_a_failed_job_allows_the_same_bytes_again(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        raw_dir: Path,
        status: ImportJobStatus,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        first = upload(client, headers, vendor).json()["job"]
        set_status(db_session, first["id"], status)

        second = upload(client, headers, vendor)

        assert second.status_code == 201, second.text
        assert second.json()["duplicate"] is False
        assert second.json()["job"]["id"] != first["id"]
        assert second.json()["job"]["import_file"]["id"] == first["import_file"]["id"]
        assert second.json()["job"]["status"] == "PENDING"
        assert len(db_session.execute(select(ImportFile)).scalars().all()) == 1
        assert len(list(raw_dir.rglob("*.csv"))) == 1  # the bytes were not stored twice
        [created] = audit_rows(db_session, uuid.UUID(second.json()["job"]["id"]))
        assert created.summary is not None and "retry" in created.summary

    @pytest.mark.parametrize(
        "status",
        [ImportJobStatus.RUNNING, ImportJobStatus.COMPLETED, ImportJobStatus.COMPLETED_WITH_ERRORS],
    )
    def test_a_live_or_completed_job_is_a_duplicate(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        status: ImportJobStatus,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        first = upload(client, headers, vendor).json()["job"]
        set_status(db_session, first["id"], status)

        second = upload(client, headers, vendor)

        assert second.status_code == 200
        assert second.json()["duplicate"] is True and second.json()["job"]["id"] == first["id"]

    def test_the_same_bytes_for_another_vendor_are_refused(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        other = factories.make_vendor(db_session, organization, code="OTHER")
        upload(client, headers, vendor)

        response = upload(client, headers, other)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "import_file_belongs_to_another_vendor"

    def test_the_same_bytes_in_another_organization_are_stored_separately(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session, raw_dir: Path
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        upload(client, headers, vendor)
        other = factories.make_organization(db_session)
        other_vendor = factories.make_vendor(db_session, other, code="ACME")
        other_file = factories.make_import_file(
            db_session, other, other_vendor, sha256=sha(CSV_BYTES)
        )
        # The unique index is per organization: the row above coexists with ours,
        # and the on-disk key is prefixed by organization, so no collision.
        assert other_file.sha256 == sha(CSV_BYTES)
        assert len(list(raw_dir.rglob("*.csv"))) == 1


# --- refusals -------------------------------------------------------------------------------


class TestRefusals:
    @pytest.mark.parametrize("name", ["prices.txt", "prices.xls", "prices", "prices.csv.exe"])
    def test_unsupported_extension_is_415(
        self, client: TestClient, login: Login, vendor: Vendor, raw_dir: Path, name: str
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = upload(client, headers, vendor, name=name)

        assert response.status_code == 415, response.text
        assert response.json()["error"]["code"] == "unsupported_file_type"
        assert not raw_dir.exists()

    def test_over_the_limit_is_413_and_nothing_is_stored(
        self, client: TestClient, login: Login, vendor: Vendor, raw_dir: Path, db_session: Session
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        content = b"UPC,Qty\r\n" + b"0" * (MAX_MB * 1024 * 1024)

        response = upload(client, headers, vendor, content=content)

        assert response.status_code == 413, response.text
        assert response.json()["error"]["code"] == "upload_too_large"
        assert not raw_dir.exists()
        assert db_session.execute(select(ImportFile)).scalars().all() == []

    def test_exactly_the_limit_is_accepted(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        content = b"0" * (MAX_MB * 1024 * 1024)

        assert upload(client, headers, vendor, content=content).status_code == 201

    def test_an_empty_file_is_422(
        self, client: TestClient, login: Login, vendor: Vendor, raw_dir: Path
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = upload(client, headers, vendor, content=b"")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "upload_empty"
        assert not raw_dir.exists()

    def test_missing_vendor_is_422_and_unknown_vendor_is_404(
        self, client: TestClient, login: Login, raw_dir: Path
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        no_vendor = client.post(IMPORTS, files={"file": ("a.csv", CSV_BYTES)}, headers=headers)
        assert no_vendor.status_code == 422
        unknown = client.post(
            IMPORTS,
            data={"vendor_id": str(uuid.uuid4())},
            files={"file": ("a.csv", CSV_BYTES)},
            headers=headers,
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "vendor_not_found"
        assert not raw_dir.exists()

    def test_unknown_job_is_404(self, client: TestClient, login: Login) -> None:
        headers, _ = login(RoleCode.VIEWER)
        response = client.get(f"{IMPORTS}/{uuid.uuid4()}", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "import_job_not_found"


# --- profiles and listing -----------------------------------------------------------------


class TestProfilesAndListing:
    def test_a_profile_is_pinned_by_id_and_version(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        profile = factories.make_import_profile(db_session, organization, vendor, version=3)

        job = upload(client, headers, vendor, profile_id=profile.id).json()["job"]

        assert job["vendor_import_profile_id"] == str(profile.id)
        assert job["profile_version"] == 3

    def test_an_inactive_profile_is_refused(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        profile = factories.make_import_profile(db_session, organization, vendor, is_active=False)

        response = upload(client, headers, vendor, profile_id=profile.id)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "import_profile_not_active"

    def test_another_vendors_profile_is_not_found(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        other = factories.make_vendor(db_session, organization, code="OTHER")
        profile = factories.make_import_profile(db_session, organization, other)

        response = upload(client, headers, vendor, profile_id=profile.id)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "import_profile_not_found"

    def test_list_filters_and_pages(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        other = factories.make_vendor(db_session, organization, code="OTHER")
        a = upload(client, headers, vendor, content=b"a,b\r\n1,2\r\n").json()["job"]
        b = upload(client, headers, other, content=b"a,b\r\n3,4\r\n").json()["job"]
        set_status(db_session, b["id"], ImportJobStatus.FAILED)

        everything = client.get(IMPORTS, headers=headers).json()
        assert everything["total"] == 2
        assert {j["id"] for j in everything["items"]} == {a["id"], b["id"]}
        assert everything["items"][0]["import_file"]["sha256"]  # nested file is present

        by_vendor = client.get(IMPORTS, params={"vendor_id": str(vendor.id)}, headers=headers)
        assert [j["id"] for j in by_vendor.json()["items"]] == [a["id"]]
        failed = client.get(IMPORTS, params={"status": "FAILED"}, headers=headers)
        assert [j["id"] for j in failed.json()["items"]] == [b["id"]]
        paged = client.get(IMPORTS, params={"page": 2, "page_size": 1}, headers=headers)
        assert paged.json()["page"] == 2 and len(paged.json()["items"]) == 1
        assert client.get(IMPORTS, params={"status": "nope"}, headers=headers).status_code == 422
