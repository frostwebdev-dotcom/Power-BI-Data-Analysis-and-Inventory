"""Import profiles end to end (AC-6.1 to AC-6.4, ADR 0013).

Versioning is the behaviour under test: editing the active version creates
version n+1 and retires n, with two audit rows in one transaction; inactive
versions are never edited. The validate endpoints are exercised with the two
sample layouts from the brief — ``UPC | Description | Quantity | Cost`` as
CSV and ``Vendor SKU | UPC | Qty Available | Wholesale Price`` as XLSX — built
in-test, since ``.gitignore`` keeps vendor files out of the repository.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Callable, Iterator
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
from app.main import create_app
from app.models import AuditEvent, Organization, User, Vendor, VendorImportProfile
from app.services.roles import assign_role, ensure_system_roles
from tests.integration import factories

pytestmark = pytest.mark.integration


@pytest.fixture
def api_settings() -> Settings:
    return Settings(
        app_env="test",
        log_level="WARNING",
        dev_auth_enabled=True,
        auth_jwt_secret=SecretStr("integration-test-signing-key-at-least-32-chars"),
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


def profiles_url(vendor: Vendor) -> str:
    return f"/api/v1/vendors/{vendor.id}/import-profiles"


# --- the two sample layouts from the brief ------------------------------------------------

CSV_HEADERS = ["UPC", "Description", "Quantity", "Cost"]
CSV_ROWS = [
    ["012345678905", "Blue Widget", "24", "$3.50"],
    ["12345678905", "Blue Widget (no leading zero)", "0", "3.50"],
    ["012345678906", "Bad check digit", "5", "4.00"],
    ["", "No UPC at all", "x", ""],
]

XLSX_HEADERS = ["Vendor SKU", "UPC", "Qty Available", "Wholesale Price"]
XLSX_ROWS: list[list[Any]] = [
    ["ACM-001", 12345678905, 100, 12.5],  # numeric UPC that lost its leading zero
    ["ACM-002", "036000291452", 0, 7],
    ["ACM-003", None, 3, 0.99],
]


def csv_sample() -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows([CSV_HEADERS, *CSV_ROWS])
    return buffer.getvalue().encode("utf-8")


def xlsx_sample() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Price List"
    sheet.append(XLSX_HEADERS)
    for row in XLSX_ROWS:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def csv_profile(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Weekly price list",
        "file_format": "CSV",
        "column_map": {
            "columns": [
                {"target": "upc", "source": "UPC"},
                {"target": "description", "source": "Description"},
                {"target": "quantity_available", "source": "Quantity"},
                {"target": "unit_cost", "source": "Cost"},
            ]
        },
        "header_signature": compute_header_signature(CSV_HEADERS),
    }
    body.update(overrides)
    return body


def xlsx_profile(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Wholesale workbook",
        "file_format": "XLSX",
        "sheet_name": "Price List",
        "column_map": {
            "columns": [
                {"target": "vendor_sku", "source": "Vendor SKU", "required": True},
                {"target": "upc", "source": "UPC"},
                {"target": "quantity_available", "source": "Qty Available"},
                {"target": "unit_cost", "source": "Wholesale Price"},
            ]
        },
        "price_semantics": {"currency": "USD", "includes_tax": False, "per": "each"},
        "header_signature": compute_header_signature(XLSX_HEADERS),
    }
    body.update(overrides)
    return body


def upload(name: str, content: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, content, "application/octet-stream")}


def audit_rows(db_session: Session, entity_id: uuid.UUID) -> list[AuditEvent]:
    """Ordered by action, not ``occurred_at`` — see test_vendor_api.audit_rows."""
    return sorted(
        db_session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == entity_id,
                AuditEvent.action.like("import_profile%"),
            )
        )
        .scalars()
        .all(),
        key=lambda e: e.action,
    )


def create(client: TestClient, headers: Headers, vendor: Vendor, body: dict[str, Any]) -> Any:
    response = client.post(profiles_url(vendor), json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --- access control -----------------------------------------------------------------------


class TestAccessControl:
    @pytest.mark.parametrize(
        ("method", "suffix"),
        [
            ("GET", ""),
            ("POST", ""),
            ("GET", f"/{uuid.uuid4()}"),
            ("PATCH", f"/{uuid.uuid4()}"),
            ("POST", f"/{uuid.uuid4()}/deactivate"),
            ("POST", f"/{uuid.uuid4()}/validate"),
            ("POST", "/validate"),
        ],
    )
    def test_every_route_requires_authentication(
        self, client: TestClient, vendor: Vendor, method: str, suffix: str
    ) -> None:
        response = client.request(method, profiles_url(vendor) + suffix, json={})

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    def test_rule_schemas_require_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/import-profiles/rule-schemas").status_code == 401

    @pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.PURCHASING_MANAGER])
    def test_read_roles_cannot_write(
        self, client: TestClient, login: Login, vendor: Vendor, role: RoleCode
    ) -> None:
        headers, _ = login(role)

        response = client.post(profiles_url(vendor), json=csv_profile(), headers=headers)

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    def test_viewer_can_read_and_validate(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        operator, _ = login(RoleCode.DATA_OPERATOR)
        created = create(client, operator, vendor, csv_profile())
        viewer, _ = login(RoleCode.VIEWER)

        assert client.get(profiles_url(vendor), headers=viewer).status_code == 200
        assert (
            client.get(f"{profiles_url(vendor)}/{created['id']}", headers=viewer).status_code == 200
        )
        preview = client.post(
            f"{profiles_url(vendor)}/{created['id']}/validate",
            files=upload("sample.csv", csv_sample()),
            headers=viewer,
        )
        assert preview.status_code == 200
        assert client.get("/api/v1/import-profiles/rule-schemas", headers=viewer).status_code == 200

    def test_admin_can_write(self, client: TestClient, login: Login, vendor: Vendor) -> None:
        admin, _ = login(RoleCode.ADMIN)
        create(client, admin, vendor, csv_profile())

    def test_another_organizations_vendor_is_not_found(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        other = factories.make_organization(db_session)
        foreign = factories.make_vendor(db_session, other, code="FOREIGN")
        headers, _ = login(RoleCode.DATA_OPERATOR)

        assert client.get(profiles_url(foreign), headers=headers).status_code == 404
        response = client.post(profiles_url(foreign), json=csv_profile(), headers=headers)
        assert response.status_code == 404
        assert db_session.execute(select(VendorImportProfile)).scalars().all() == []


# --- create ---------------------------------------------------------------------------------


class TestCreate:
    def test_creates_version_one_with_defaults_and_an_audit_row(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session
    ) -> None:
        headers, user = login(RoleCode.DATA_OPERATOR)

        body = create(client, headers, vendor, csv_profile())

        assert body["version"] == 1 and body["is_active"] is True
        assert body["vendor_id"] == str(vendor.id)
        assert body["created_by_user_id"] == str(user.id)
        assert body["column_map"]["columns"][0] == {
            "target": "upc",
            "source": "UPC",
            "required": False,
        }
        assert body["normalization_rules"]["upc_pad_to_12"] is True
        assert body["availability_rules"]["available_when"] == "quantity_gt_zero"
        assert body["quantity_semantics"] == {
            "unit": "each",
            "case_pack_from": "pack_size_column",
            "fixed_pack_size": None,
        }
        assert body["price_semantics"] == {"currency": "USD", "includes_tax": False, "per": "each"}
        assert body["pack_size_handling"] == {"mode": "store_as_stated"}
        assert body["header_signature"] == compute_header_signature(CSV_HEADERS)

        [event] = audit_rows(db_session, uuid.UUID(body["id"]))
        assert event.action == "import_profile.created"
        assert event.actor_user_id == user.id
        assert event.entity_type == "vendor_import_profiles"
        assert event.before is None
        assert event.after is not None and event.after["version"] == 1

    @pytest.mark.parametrize(
        ("overrides", "fragment"),
        [
            (
                {"column_map": {"columns": [{"target": "upc", "source": "UPC"}]}},
                "quantity_available",
            ),
            (
                {
                    "column_map": {
                        "columns": [
                            {"target": "description", "source": "D"},
                            {"target": "quantity_available", "source": "Q"},
                        ]
                    }
                },
                "upc or vendor_sku",
            ),
            (
                {"normalization_rules": {"decimal_separator": ",", "thousands_separator": ","}},
                "differ",
            ),
            (
                {"availability_rules": {"available_when": "status_column"}},
                "status_column is required",
            ),
            (
                {"quantity_semantics": {"unit": "case", "case_pack_from": "fixed"}},
                "fixed_pack_size",
            ),
            ({"quantity_semantics": {"unit": "case"}}, "maps no pack_size"),
            ({"price_semantics": {"currency": "US Dollars"}}, "ISO 4217"),
            ({"pack_size_handling": {"mode": "normalize_to_each"}}, "store_as_stated"),
            ({"header_signature": "abc"}, "header_signature"),
            ({"sheet_name": "Sheet1"}, "XLSX"),
            (
                {
                    "column_map": {
                        "columns": [
                            {"target": "upc", "source": "UPC", "regex": ".*"},
                            {"target": "quantity_available", "source": "Q"},
                        ]
                    }
                },
                "extra",
            ),
        ],
    )
    def test_every_rule_shape_is_enforced_at_the_boundary(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        overrides: dict[str, Any],
        fragment: str,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.post(profiles_url(vendor), json=csv_profile(**overrides), headers=headers)

        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert fragment in json.dumps(error), error
        assert db_session.execute(select(VendorImportProfile)).scalars().all() == []

    def test_an_active_name_cannot_be_reused(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        create(client, headers, vendor, csv_profile())

        response = client.post(profiles_url(vendor), json=csv_profile(), headers=headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "import_profile_name_taken"

    def test_the_same_name_may_exist_for_another_vendor(
        self,
        client: TestClient,
        login: Login,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        other = factories.make_vendor(db_session, organization, code="OTHER")
        create(client, headers, vendor, csv_profile())
        create(client, headers, other, csv_profile())

    def test_creating_after_deactivation_continues_the_version_line(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        """Versions are per (vendor, name) for all time, not per active run."""
        headers, _ = login(RoleCode.DATA_OPERATOR)
        first = create(client, headers, vendor, csv_profile())
        client.post(f"{profiles_url(vendor)}/{first['id']}/deactivate", headers=headers)

        second = create(client, headers, vendor, csv_profile())

        assert second["version"] == 2 and second["id"] != first["id"]


# --- versioning ---------------------------------------------------------------------------


class TestVersioning:
    def test_editing_creates_the_next_version_and_retires_the_current_one(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session
    ) -> None:
        headers, user = login(RoleCode.DATA_OPERATOR)
        v1 = create(client, headers, vendor, csv_profile())

        response = client.patch(
            f"{profiles_url(vendor)}/{v1['id']}",
            json={"normalization_rules": {"thousands_separator": ","}, "skip_rows": 1},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        v2 = response.json()
        assert v2["id"] != v1["id"]
        assert v2["version"] == 2 and v2["is_active"] is True
        assert v2["name"] == v1["name"]
        # Changed fields applied; everything else carried over from v1.
        assert v2["skip_rows"] == 1
        assert v2["normalization_rules"]["thousands_separator"] == ","
        assert v2["normalization_rules"]["upc_pad_to_12"] is True
        assert v2["column_map"] == v1["column_map"]
        assert v2["header_signature"] == v1["header_signature"]
        assert v2["created_by_user_id"] == str(user.id)

        # v1 is retired but intact: an import that ran under it still reads it.
        stale = client.get(f"{profiles_url(vendor)}/{v1['id']}", headers=headers).json()
        assert stale["is_active"] is False and stale["version"] == 1
        assert stale["normalization_rules"]["thousands_separator"] == ""

        [created] = audit_rows(db_session, uuid.UUID(v2["id"]))
        assert created.action == "import_profile.version_created"
        assert created.summary is not None and "normalization_rules, skip_rows" in created.summary
        old_created, superseded = audit_rows(db_session, uuid.UUID(v1["id"]))
        assert old_created.action == "import_profile.created"
        assert superseded.action == "import_profile.superseded"
        assert superseded.before is not None and superseded.before["is_active"] is True
        assert superseded.after is not None and superseded.after["is_active"] is False
        assert superseded.actor_user_id == user.id

    def test_the_list_shows_the_active_version_only_unless_asked(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        v1 = create(client, headers, vendor, csv_profile())
        client.patch(f"{profiles_url(vendor)}/{v1['id']}", json={"skip_rows": 1}, headers=headers)
        client.patch(
            f"{profiles_url(vendor)}/"
            + client.get(profiles_url(vendor), headers=headers).json()["items"][0]["id"],
            json={"skip_rows": 2},
            headers=headers,
        )

        active = client.get(profiles_url(vendor), headers=headers).json()
        everything = client.get(
            profiles_url(vendor), params={"include_inactive": "true"}, headers=headers
        ).json()

        assert active["total"] == 1 and active["items"][0]["version"] == 3
        assert everything["total"] == 3
        assert [p["version"] for p in everything["items"]] == [3, 2, 1]
        assert [p["is_active"] for p in everything["items"]] == [True, False, False]

    def test_an_inactive_version_cannot_be_edited(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        v1 = create(client, headers, vendor, csv_profile())
        client.patch(f"{profiles_url(vendor)}/{v1['id']}", json={"skip_rows": 1}, headers=headers)

        response = client.patch(
            f"{profiles_url(vendor)}/{v1['id']}", json={"skip_rows": 5}, headers=headers
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "import_profile_not_active"

    def test_an_empty_edit_creates_no_version(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        v1 = create(client, headers, vendor, csv_profile())

        response = client.patch(f"{profiles_url(vendor)}/{v1['id']}", json={}, headers=headers)

        assert response.status_code == 200
        assert response.json()["id"] == v1["id"] and response.json()["version"] == 1

    def test_an_edit_that_breaks_a_cross_rule_is_refused_whole(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session
    ) -> None:
        """Version n+1 is validated as a whole against n's carried-over rules."""
        headers, _ = login(RoleCode.DATA_OPERATOR)
        v1 = create(client, headers, vendor, csv_profile())

        response = client.patch(
            f"{profiles_url(vendor)}/{v1['id']}",
            json={"quantity_semantics": {"unit": "case", "case_pack_from": "pack_size_column"}},
            headers=headers,
        )

        assert response.status_code == 422
        assert "maps no pack_size" in json.dumps(response.json())
        current = client.get(f"{profiles_url(vendor)}/{v1['id']}", headers=headers).json()
        assert current["is_active"] is True
        assert len(db_session.execute(select(VendorImportProfile)).scalars().all()) == 1

    def test_deactivate_keeps_the_row_and_audits(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session
    ) -> None:
        headers, user = login(RoleCode.DATA_OPERATOR)
        v1 = create(client, headers, vendor, csv_profile())

        response = client.post(f"{profiles_url(vendor)}/{v1['id']}/deactivate", headers=headers)

        assert response.status_code == 200
        assert response.json()["is_active"] is False
        again = client.post(f"{profiles_url(vendor)}/{v1['id']}/deactivate", headers=headers)
        assert again.status_code == 409
        _created, deactivated = audit_rows(db_session, uuid.UUID(v1["id"]))
        assert deactivated.action == "import_profile.deactivated"
        assert deactivated.actor_user_id == user.id

    def test_unknown_profile_is_404(self, client: TestClient, login: Login, vendor: Vendor) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.get(f"{profiles_url(vendor)}/{uuid.uuid4()}", headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "import_profile_not_found"


# --- validate against a sample file ------------------------------------------------------


class TestValidate:
    def test_csv_sample_against_the_stored_profile(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        stored = create(client, headers, vendor, csv_profile())

        response = client.post(
            f"{profiles_url(vendor)}/{stored['id']}/validate",
            files=upload("weekly.csv", csv_sample()),
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["file_name"] == "weekly.csv"
        assert body["encoding"] == "utf-8" and body["sheet"] is None
        assert body["headers"] == CSV_HEADERS
        assert body["signature_matches"] is True and body["header_ok"] is True
        assert body["issues"] == [] and body["truncated"] is False
        assert [c["index"] for c in body["columns"]] == [0, 1, 2, 3]

        rows = body["rows"]
        assert len(rows) == 4
        assert rows[0]["values"]["upc"] == "00012345678905"
        assert rows[0]["values"]["quantity"] == 24
        assert rows[0]["values"]["unit_cost"] == "3.50"  # "$" stripped by default
        assert rows[0]["values"]["availability"] == "AVAILABLE" and rows[0]["issues"] == []
        assert rows[1]["values"]["upc"] == "00012345678905"  # leading zero restored
        assert rows[1]["values"]["availability"] == "OUT_OF_STOCK"
        assert rows[2]["values"]["upc_valid_checksum"] is False
        assert [i["code"] for i in rows[2]["issues"]] == ["UPC_INVALID"]
        assert rows[3]["values"]["quantity"] is None
        assert rows[3]["values"]["availability"] == "UNKNOWN"
        assert [i["code"] for i in rows[3]["issues"]] == ["IDENTIFIER_MISSING", "QUANTITY_INVALID"]
        assert rows[3]["status"] == "ERROR" and rows[0]["status"] == "OK"
        # Nothing was stored.
        assert len(db_session.execute(select(VendorImportProfile)).scalars().all()) == 1

    def test_xlsx_sample_against_a_draft_profile(
        self, client: TestClient, login: Login, vendor: Vendor, db_session: Session
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.post(
            f"{profiles_url(vendor)}/validate",
            data={"profile": json.dumps(xlsx_profile())},
            files=upload("wholesale.xlsx", xlsx_sample()),
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sheet"] == "Price List" and body["encoding"] is None
        assert body["headers"] == XLSX_HEADERS
        assert body["signature_matches"] is True and body["header_ok"] is True

        rows = body["rows"]
        assert [r["values"]["vendor_sku"] for r in rows] == ["ACM-001", "ACM-002", "ACM-003"]
        # The numeric cell 12345678905 is read as digits, not 1.23457E+10.
        assert rows[0]["raw"]["upc"] == "12345678905"
        assert rows[0]["values"]["upc"] == "00012345678905"
        assert rows[0]["values"]["quantity"] == 100
        assert rows[0]["values"]["unit_cost"] == "12.5"
        assert rows[1]["values"]["upc"] == "00036000291452"
        assert rows[1]["values"]["availability"] == "OUT_OF_STOCK"
        assert rows[2]["raw"]["upc"] == "" and rows[2]["values"]["upc"] is None
        assert rows[2]["values"]["unit_cost"] == "0.99"
        assert db_session.execute(select(VendorImportProfile)).scalars().all() == []

    def test_a_file_with_different_columns_fails_the_signature(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        stored = create(client, headers, vendor, csv_profile())
        renamed = csv_sample().replace(b"Quantity", b"Qty")

        response = client.post(
            f"{profiles_url(vendor)}/{stored['id']}/validate",
            files=upload("renamed.csv", renamed),
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["signature_matches"] is False
        assert body["header_ok"] is False  # quantity_available is always required
        assert any("header signature does not match" in i for i in body["issues"])
        assert any("required column for quantity_available" in i for i in body["issues"])
        assert body["rows"][0]["values"]["quantity"] is None

    def test_an_unpinned_draft_reports_the_signature_to_pin(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.post(
            f"{profiles_url(vendor)}/validate",
            data={"profile": json.dumps(csv_profile(header_signature=None))},
            files=upload("weekly.csv", csv_sample()),
            headers=headers,
        )

        body = response.json()
        assert body["signature_matches"] is None
        assert body["header_signature"] == compute_header_signature(CSV_HEADERS)

    def test_preview_is_capped_at_twenty_rows(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        big = csv_sample() + b"".join(f"01234567890{i % 10},x,1,1\n".encode() for i in range(40))

        response = client.post(
            f"{profiles_url(vendor)}/validate",
            data={"profile": json.dumps(csv_profile())},
            files=upload("big.csv", big),
            headers=headers,
        )

        assert len(response.json()["rows"]) == 20
        assert response.json()["truncated"] is True

    def test_an_unreadable_sample_is_422(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.post(
            f"{profiles_url(vendor)}/validate",
            data={"profile": json.dumps(xlsx_profile())},
            files=upload("not-a-workbook.xlsx", b"this is not a zip file"),
            headers=headers,
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "sample_file_unreadable"
        assert "XLSX" in error["details"]["reason"]

    def test_a_wrong_sheet_name_is_422(
        self, client: TestClient, login: Login, vendor: Vendor
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.post(
            f"{profiles_url(vendor)}/validate",
            data={"profile": json.dumps(xlsx_profile(sheet_name="Inventory"))},
            files=upload("wholesale.xlsx", xlsx_sample()),
            headers=headers,
        )

        assert response.status_code == 422
        assert "not found" in response.json()["error"]["details"]["reason"]

    @pytest.mark.parametrize(
        ("profile", "fragment"),
        [
            ("not json", "not JSON"),
            (json.dumps({"name": "x"}), "column_map"),
            (
                json.dumps(
                    csv_profile(
                        column_map={
                            "columns": [
                                {"target": "upc", "source": "UPC"},
                                {"target": "quantity_available", "source": "Quantity"},
                            ]
                        },
                        price_semantics={"per": "case"},
                    )
                ),
                "maps no unit_cost",
            ),
        ],
    )
    def test_an_invalid_draft_is_422(
        self, client: TestClient, login: Login, vendor: Vendor, profile: str, fragment: str
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.post(
            f"{profiles_url(vendor)}/validate",
            data={"profile": profile},
            files=upload("weekly.csv", csv_sample()),
            headers=headers,
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "validation_failed"
        assert fragment in json.dumps(response.json()), response.text

    def test_rule_schemas_describe_all_six_columns(self, client: TestClient, login: Login) -> None:
        headers, _ = login(RoleCode.VIEWER)

        response = client.get("/api/v1/import-profiles/rule-schemas", headers=headers)

        schemas = response.json()["schemas"]
        assert set(schemas) == {
            "column_map",
            "normalization_rules",
            "availability_rules",
            "quantity_semantics",
            "price_semantics",
            "pack_size_handling",
        }
        assert schemas["column_map"]["$defs"]["ColumnTarget"]["enum"] == [
            "vendor_sku",
            "upc",
            "description",
            "quantity_available",
            "unit_cost",
            "pack_size",
            "unit_of_measure",
            "ignore",
        ]
