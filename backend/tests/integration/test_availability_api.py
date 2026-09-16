"""Snapshots, availability events and the OOS watchlist end to end
(AC-10.1, 10.2, 10.4; AC-11.1 to 11.4, 11.6, 11.7; Milestone 1 exit criterion 9).
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from app.models.enums import AvailabilityEventType, AvailabilityStatus, ImportJobStatus, OosStatus
from app.models.ingestion import ImportJob
from app.models.inventory import AvailabilityEvent, VendorInventorySnapshot
from app.models.watchlist import OosStatusHistory, OosWatchlistEntry
from app.services.roles import assign_role, ensure_system_roles
from app.services.snapshots import transition
from tests.integration import factories

pytestmark = pytest.mark.integration

WATCHLIST = "/api/v1/watchlist"
EVENTS = "/api/v1/availability/events"
IMPORTS = "/api/v1/imports"
UPC_A = "00012345678905"
UPC_B = "00036000291452"


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

HEADERS = ["SKU", "UPC", "Desc", "Qty", "Cost"]
PROFILE: dict[str, Any] = {
    "name": "stock-profile",
    "file_format": "CSV",
    "column_map": {
        "columns": [
            {"target": "vendor_sku", "source": "SKU"},
            {"target": "upc", "source": "UPC"},
            {"target": "description", "source": "Desc"},
            {"target": "quantity_available", "source": "Qty"},
            {"target": "unit_cost", "source": "Cost"},
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
    name: str,
    headers: list[str] | None = None,
    expect: int = 201,
) -> dict[str, Any]:
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
    assert response.status_code == expect, response.text
    job: dict[str, Any] = response.json()["job"]
    return job


def events_of(db_session: Session, organization: Organization) -> list[AvailabilityEvent]:
    return list(
        db_session.execute(
            select(AvailabilityEvent)
            .where(AvailabilityEvent.organization_id == organization.id)
            .order_by(AvailabilityEvent.detected_at, AvailabilityEvent.id)
        ).scalars()
    )


def snapshots_of(db_session: Session, organization: Organization) -> list[VendorInventorySnapshot]:
    return list(
        db_session.execute(
            select(VendorInventorySnapshot)
            .where(VendorInventorySnapshot.organization_id == organization.id)
            .order_by(VendorInventorySnapshot.captured_at, VendorInventorySnapshot.id)
        ).scalars()
    )


def status_of(db_session: Session, entry_id: str) -> str:
    """The entry's current status re-read from the database (mypy cannot see
    that ``refresh`` changes it)."""
    db_session.expire_all()
    entry = db_session.get(OosWatchlistEntry, uuid.UUID(entry_id))
    assert entry is not None
    return entry.current_status.value


def watch(
    client: TestClient, headers: Headers, product_id: uuid.UUID, **extra: Any
) -> dict[str, Any]:
    response = client.post(
        WATCHLIST, json={"product_id": str(product_id), **extra}, headers=headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- exit criterion 9 -----------------------------------------------------------------------


class TestProductBecomesAvailable:
    def test_watch_then_zero_then_forty_raises_exactly_one_linked_event(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, buyer = manager
        product = factories.make_product(db_session, organization, name="Product ABC")
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        db_session.flush()

        # 1. Watch it.
        entry = watch(
            client,
            headers,
            product.id,
            reason="Best seller, out everywhere",
            desired_quantity="24",
            max_unit_cost="5.00",
            priority="HIGH",
        )
        assert entry["current_status"] == "UNKNOWN"  # nothing observed yet
        assert entry["added_by_user_id"] == str(buyer.id)

        # 2. Vendor B shows 0.
        first = import_rows(
            client,
            operator,
            vendor,
            [["B-1", "012345678905", "Product ABC", "0", "4.50"]],
            name="mon.csv",
        )
        assert first["status"] == "COMPLETED" and first["current_stage"] is None
        assert first["matched_rows"] == 1
        [snapshot] = snapshots_of(db_session, organization)
        assert snapshot.availability_status is AvailabilityStatus.OUT_OF_STOCK
        assert snapshot.quantity_available == Decimal("0") and snapshot.product_id == product.id
        assert events_of(db_session, organization) == []
        assert status_of(db_session, entry["id"]) == "UNKNOWN"

        # 3. Vendor B shows 40 → "Product ABC is now available from Vendor B".
        second = import_rows(
            client,
            operator,
            vendor,
            [["B-1", "012345678905", "Product ABC", "40", "4.50"]],
            name="tue.csv",
        )
        assert second["status"] == "COMPLETED"
        assert second["error_details"]["snapshotting"] == {
            "snapshots": 1,
            "events": {"BECAME_AVAILABLE": 1},
            "watchlist_hits": 1,
            "over_max_unit_cost": 0,
            "effective_at": second["error_details"]["snapshotting"]["effective_at"],
            "chunks": 1,
        }
        [event] = events_of(db_session, organization)
        assert event.event_type is AvailabilityEventType.BECAME_AVAILABLE
        assert event.oos_watchlist_id == uuid.UUID(entry["id"])
        assert event.product_id == product.id and event.vendor_id == vendor.id
        assert event.previous_snapshot_id == snapshot.id
        assert event.current_snapshot_id != snapshot.id
        assert (event.previous_status, event.new_status) == (
            AvailabilityStatus.OUT_OF_STOCK,
            AvailabilityStatus.AVAILABLE,
        )
        assert (event.previous_quantity, event.new_quantity) == (Decimal("0"), Decimal("40"))
        assert event.over_max_unit_cost is False  # 4.50 <= 5.00
        assert status_of(db_session, entry["id"]) == "IN_STOCK"
        record = db_session.get(OosWatchlistEntry, uuid.UUID(entry["id"]))
        assert record is not None and record.current_status_changed_at is not None
        history = list(
            db_session.execute(
                select(OosStatusHistory).where(OosStatusHistory.oos_watchlist_id == record.id)
            ).scalars()
        )
        assert len(history) == 1
        assert (history[0].previous_status, history[0].new_status) == (
            OosStatus.UNKNOWN,
            OosStatus.IN_STOCK,
        )
        assert history[0].vendor_inventory_snapshot_id == event.current_snapshot_id
        # The feed shows it, prominently filterable to the watchlist.
        feed = client.get(EVENTS, params={"watchlist_only": "true"}, headers=headers).json()
        assert feed["total"] == 1
        assert feed["items"][0]["event_type"] == "BECAME_AVAILABLE"
        assert feed["items"][0]["vendor_sku"] == "B-1"
        assert feed["items"][0]["product_name"] == "Product ABC"
        assert feed["items"][0]["oos_watchlist_id"] == entry["id"]
        hist = client.get(f"{WATCHLIST}/{entry['id']}/history", headers=headers).json()
        assert hist["entry"]["current_status"] == "IN_STOCK"
        assert [(h["previous_status"], h["new_status"]) for h in hist["history"]] == [
            ("UNKNOWN", "IN_STOCK")
        ]
        # Audit entries exist for the watch and the completed jobs.
        actions = set(
            db_session.execute(
                select(AuditEvent.action).where(AuditEvent.organization_id == organization.id)
            ).scalars()
        )
        assert {"watchlist.added", "import_job.completed", "import_job.matched"} <= actions

        # 4. The second file a third time: identical bytes are a duplicate upload…
        again = import_rows(
            client,
            operator,
            vendor,
            [["B-1", "012345678905", "Product ABC", "40", "4.50"]],
            name="tue-again.csv",
            expect=200,
        )
        assert again["id"] == second["id"]
        # …and a byte-different file with the same state is a new job with no new event.
        third = import_rows(
            client,
            operator,
            vendor,
            [["B-1", "012345678905", "Product ABC", "40", "4.50"]],
            name="wed.csv",
            headers=[h.lower() for h in HEADERS],
        )
        assert third["status"] == "COMPLETED"
        assert third["error_details"]["snapshotting"]["events"] == {}
        assert len(events_of(db_session, organization)) == 1
        assert len(snapshots_of(db_session, organization)) == 3
        assert status_of(db_session, entry["id"]) == "IN_STOCK"
        assert (
            len(
                db_session.execute(
                    select(OosStatusHistory).where(
                        OosStatusHistory.oos_watchlist_id == uuid.UUID(entry["id"])
                    )
                )
                .scalars()
                .all()
            )
            == 1
        )


# --- the state machine ------------------------------------------------------------------------


class TestTransitions:
    @pytest.mark.parametrize(
        ("previous", "current", "expected"),
        [
            (None, AvailabilityStatus.AVAILABLE, AvailabilityEventType.BECAME_AVAILABLE),
            (None, AvailabilityStatus.OUT_OF_STOCK, None),
            (None, AvailabilityStatus.UNKNOWN, None),
            (
                AvailabilityStatus.OUT_OF_STOCK,
                AvailabilityStatus.AVAILABLE,
                AvailabilityEventType.BECAME_AVAILABLE,
            ),
            (
                AvailabilityStatus.UNKNOWN,
                AvailabilityStatus.AVAILABLE,
                AvailabilityEventType.BECAME_AVAILABLE,
            ),
            (
                AvailabilityStatus.DISCONTINUED,
                AvailabilityStatus.AVAILABLE,
                AvailabilityEventType.BECAME_AVAILABLE,
            ),
            (AvailabilityStatus.AVAILABLE, AvailabilityStatus.AVAILABLE, None),
            (
                AvailabilityStatus.AVAILABLE,
                AvailabilityStatus.OUT_OF_STOCK,
                AvailabilityEventType.BECAME_UNAVAILABLE,
            ),
            (
                AvailabilityStatus.AVAILABLE,
                AvailabilityStatus.DISCONTINUED,
                AvailabilityEventType.BECAME_UNAVAILABLE,
            ),
            (AvailabilityStatus.AVAILABLE, AvailabilityStatus.UNKNOWN, None),
            (AvailabilityStatus.OUT_OF_STOCK, AvailabilityStatus.OUT_OF_STOCK, None),
            (AvailabilityStatus.OUT_OF_STOCK, AvailabilityStatus.UNKNOWN, None),
        ],
    )
    def test_transition(
        self,
        previous: AvailabilityStatus | None,
        current: AvailabilityStatus,
        expected: AvailabilityEventType | None,
    ) -> None:
        assert transition(previous, current) is expected


class TestSnapshotsAndEvents:
    def test_reverse_transition_and_unmatched_lines(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        product = factories.make_product(db_session, organization)
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        db_session.flush()

        first = import_rows(
            client,
            operator,
            vendor,
            [
                ["M-1", "012345678905", "Matched", "10", "1"],
                ["U-1", "", "Unmatched line", "3", "1"],
            ],
            name="a.csv",
        )
        assert first["status"] == "COMPLETED"
        events = events_of(db_session, organization)
        # Both lines were first seen with stock: both raise BECAME_AVAILABLE, the
        # unmatched one with a null product (AC-11.7).
        assert [e.event_type for e in events] == [AvailabilityEventType.BECAME_AVAILABLE] * 2
        by_sku = {e.vendor_product.vendor_sku: e for e in events}
        assert by_sku["M-1"].product_id == product.id and by_sku["U-1"].product_id is None
        assert by_sku["U-1"].oos_watchlist_id is None
        assert len(snapshots_of(db_session, organization)) == 2

        second = import_rows(
            client,
            operator,
            vendor,
            [["M-1", "012345678905", "Matched", "0", "1"], ["U-1", "", "Unmatched line", "3", "1"]],
            name="b.csv",
        )
        assert second["error_details"]["snapshotting"]["events"] == {"BECAME_UNAVAILABLE": 1}
        events = events_of(db_session, organization)
        assert len(events) == 3
        reverse = events[-1]
        assert reverse.event_type is AvailabilityEventType.BECAME_UNAVAILABLE
        assert reverse.vendor_product.vendor_sku == "M-1"
        assert reverse.previous_snapshot_id == by_sku["M-1"].current_snapshot_id
        assert (reverse.previous_quantity, reverse.new_quantity) == (Decimal("10"), Decimal("0"))

    def test_a_job_with_rejected_rows_completes_with_errors(
        self, client: TestClient, operator: Headers, vendor: Vendor
    ) -> None:
        job = import_rows(
            client,
            operator,
            vendor,
            [["S-1", "", "ok", "1", "1"], ["S-2", "", "bad", "lots", "1"]],
            name="e.csv",
        )
        assert job["status"] == "COMPLETED_WITH_ERRORS" and job["error_rows"] == 1
        assert job["completed_at"] is not None

    def test_snapshot_carries_the_files_moment_and_the_cost(
        self,
        client: TestClient,
        operator: Headers,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        job = import_rows(
            client, operator, vendor, [["S-1", "", "x", "2", "$1,299.50"]], name="c.csv"
        )
        [snapshot] = snapshots_of(db_session, organization)
        record = db_session.get(ImportJob, uuid.UUID(job["id"]))
        assert record is not None
        assert snapshot.effective_at == record.import_file.uploaded_at
        assert snapshot.unit_cost is None  # "$1,299.50" needs the thousands rule: PRICE_INVALID
        assert snapshot.currency is None
        assert snapshot.quantity_available == Decimal("2")
        assert record.status is ImportJobStatus.COMPLETED


# --- the watchlist API ---------------------------------------------------------------------------


class TestWatchlistApi:
    def test_add_list_remove_with_audit(
        self,
        client: TestClient,
        manager: tuple[Headers, User],
        login: Login,
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, buyer = manager
        product = factories.make_product(db_session, organization, name="Watched")
        other = factories.make_product(db_session, organization, name="Other")

        entry = watch(client, headers, product.id, vendor_id=str(vendor.id), reason="Low stock")
        assert entry["vendor_id"] == str(vendor.id) and entry["priority"] == "NORMAL"
        assert entry["product"]["name"] == "Watched"
        assert entry["is_active"] is True and entry["added_at"] is not None
        duplicate = client.post(
            WATCHLIST,
            json={"product_id": str(product.id), "vendor_id": str(vendor.id)},
            headers=headers,
        )
        assert (
            duplicate.status_code == 409
            and duplicate.json()["error"]["code"] == "watchlist_entry_exists"
        )
        all_vendors = watch(client, headers, product.id)  # a different, all-vendors watch
        watch(client, headers, other.id)

        listed = client.get(WATCHLIST, headers=headers).json()
        assert listed["total"] == 3
        assert (
            client.get(WATCHLIST, params={"vendor_id": str(vendor.id)}, headers=headers).json()[
                "total"
            ]
            == 1
        )
        assert (
            client.get(WATCHLIST, params={"product_id": str(product.id)}, headers=headers).json()[
                "total"
            ]
            == 2
        )

        removed = client.post(
            f"{WATCHLIST}/{all_vendors['id']}/remove",
            json={"note": "Bought elsewhere"},
            headers=headers,
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["is_active"] is False
        assert removed.json()["deactivated_by_user_id"] == str(buyer.id)
        assert removed.json()["deactivated_at"] is not None
        assert client.get(WATCHLIST, headers=headers).json()["total"] == 2
        assert (
            client.get(WATCHLIST, params={"include_inactive": "true"}, headers=headers).json()[
                "total"
            ]
            == 3
        )
        again = client.post(f"{WATCHLIST}/{all_vendors['id']}/remove", headers=headers)
        assert again.status_code == 409
        # The row still exists (deactivated, never deleted).
        assert db_session.get(OosWatchlistEntry, uuid.UUID(all_vendors["id"])) is not None

        actions = sorted(
            db_session.execute(
                select(AuditEvent.action).where(
                    AuditEvent.entity_id == uuid.UUID(all_vendors["id"])
                )
            ).scalars()
        )
        assert actions == ["watchlist.added", "watchlist.removed"]

        viewer, _ = login(RoleCode.VIEWER)
        assert client.get(WATCHLIST, headers=viewer).status_code == 200
        assert (
            client.post(WATCHLIST, json={"product_id": str(other.id)}, headers=viewer).status_code
            == 403
        )
        assert client.post(f"{WATCHLIST}/{entry['id']}/remove", headers=viewer).status_code == 403
        assert client.get(WATCHLIST).status_code == 401

    def test_unknown_product_or_vendor_is_404_and_bad_values_422(
        self,
        client: TestClient,
        manager: tuple[Headers, User],
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = manager
        product = factories.make_product(db_session, organization)
        assert (
            client.post(
                WATCHLIST, json={"product_id": str(uuid.uuid4())}, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.post(
                WATCHLIST,
                json={"product_id": str(product.id), "vendor_id": str(uuid.uuid4())},
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.post(
                WATCHLIST,
                json={"product_id": str(product.id), "desired_quantity": "0"},
                headers=headers,
            ).status_code
            == 422
        )
        assert client.get(f"{WATCHLIST}/{uuid.uuid4()}", headers=headers).status_code == 404

    def test_a_new_watch_starts_from_the_latest_snapshots(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = manager
        product = factories.make_product(db_session, organization)
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        db_session.flush()
        import_rows(
            client, operator, vendor, [["S-1", "012345678905", "x", "7", "1"]], name="a.csv"
        )

        entry = watch(client, headers, product.id)

        assert entry["current_status"] == "IN_STOCK"

    def test_max_unit_cost_flags_but_never_drops(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = manager
        product = factories.make_product(db_session, organization)
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        db_session.flush()
        entry = watch(client, headers, product.id, max_unit_cost="2.00")

        import_rows(
            client, operator, vendor, [["S-1", "012345678905", "x", "7", "9.99"]], name="a.csv"
        )

        [event] = events_of(db_session, organization)
        assert event.oos_watchlist_id == uuid.UUID(entry["id"])
        assert event.over_max_unit_cost is True
        feed = client.get(EVENTS, headers=headers).json()
        assert feed["items"][0]["over_max_unit_cost"] is True
        assert status_of(db_session, entry["id"]) == "IN_STOCK"

    def test_unavailable_at_one_vendor_is_out_of_stock_only_when_no_vendor_has_it(
        self,
        client: TestClient,
        operator: Headers,
        manager: tuple[Headers, User],
        vendor: Vendor,
        db_session: Session,
        organization: Organization,
    ) -> None:
        headers, _ = manager
        product = factories.make_product(db_session, organization)
        factories.make_identifier(db_session, organization, product, normalized_value=UPC_A)
        other_vendor = factories.make_vendor(db_session, organization, code="OTHER")
        db_session.flush()
        entry = watch(client, headers, product.id)  # any vendor
        import_rows(
            client, operator, vendor, [["A-1", "012345678905", "x", "5", "1"]], name="a.csv"
        )
        import_rows(
            client, operator, other_vendor, [["O-1", "012345678905", "x", "5", "1"]], name="o.csv"
        )
        assert status_of(db_session, entry["id"]) == "IN_STOCK"

        import_rows(
            client, operator, vendor, [["A-1", "012345678905", "x", "0", "1"]], name="a2.csv"
        )
        assert status_of(db_session, entry["id"]) == "IN_STOCK"  # the other vendor still has it

        import_rows(
            client, operator, other_vendor, [["O-1", "012345678905", "x", "0", "1"]], name="o2.csv"
        )
        assert status_of(db_session, entry["id"]) == "OUT_OF_STOCK"
        hist = client.get(f"{WATCHLIST}/{entry['id']}/history", headers=headers).json()["history"]
        assert [(h["previous_status"], h["new_status"]) for h in hist] == [
            ("UNKNOWN", "IN_STOCK"),
            ("IN_STOCK", "OUT_OF_STOCK"),
        ]


class TestEventFeed:
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
        watched = factories.make_product(db_session, organization, name="Watched")
        plain = factories.make_product(db_session, organization, name="Plain")
        factories.make_identifier(db_session, organization, watched, normalized_value=UPC_A)
        factories.make_identifier(db_session, organization, plain, normalized_value=UPC_B)
        other_vendor = factories.make_vendor(db_session, organization, code="OTHER")
        db_session.flush()
        watch(client, headers, watched.id)
        import_rows(
            client,
            operator,
            vendor,
            [["W-1", "012345678905", "w", "5", "1"], ["P-1", "036000291452", "p", "5", "1"]],
            name="a.csv",
        )
        import_rows(
            client, operator, other_vendor, [["O-1", "036000291452", "p", "5", "1"]], name="o.csv"
        )

        assert client.get(EVENTS, headers=headers).json()["total"] == 3
        assert (
            client.get(EVENTS, params={"watchlist_only": "true"}, headers=headers).json()["total"]
            == 1
        )
        assert (
            client.get(EVENTS, params={"vendor_id": str(other_vendor.id)}, headers=headers).json()[
                "total"
            ]
            == 1
        )
        assert (
            client.get(EVENTS, params={"product_id": str(plain.id)}, headers=headers).json()[
                "total"
            ]
            == 2
        )
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        assert client.get(EVENTS, params={"since": future}, headers=headers).json()["total"] == 0
        assert client.get(EVENTS).status_code == 401
        foreign = factories.make_organization(db_session)
        foreign_vendor = factories.make_vendor(db_session, foreign, code="F")
        line = factories.make_vendor_product(db_session, foreign, foreign_vendor)
        file = factories.make_import_file(db_session, foreign, foreign_vendor)
        job = factories.make_import_job(db_session, foreign, foreign_vendor, file)
        snap = factories.make_snapshot(db_session, foreign, foreign_vendor, line, job)
        factories.make_availability_event(db_session, foreign, foreign_vendor, line, snap)
        assert client.get(EVENTS, headers=headers).json()["total"] == 3  # tenant-scoped
