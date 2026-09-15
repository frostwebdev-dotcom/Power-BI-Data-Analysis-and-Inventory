"""The vendor API end to end against a real database (AC-4.1 to AC-4.4).

Every route, every role, every refusal — and after every mutation, exactly
one audit row naming the acting user with before/after state.
"""

from __future__ import annotations

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
from app.main import create_app
from app.models import AuditEvent, Organization, User, Vendor, VendorContact
from app.models.enums import ActorType, VendorStatus
from app.services.roles import assign_role, ensure_system_roles
from tests.integration import factories

pytestmark = pytest.mark.integration

VENDORS = "/api/v1/vendors"


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


Headers = dict[str, str]
Login = Callable[[RoleCode], tuple[Headers, User]]


@pytest.fixture
def login(client: TestClient, db_session: Session, organization: Organization) -> Login:
    """Create a user with one role and return bearer headers for them."""
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


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": "ACME",
        "name": "Acme Distribution",
        "currency": "USD",
        "contact_email": "sales@acme.test",
        "minimum_order_quantity": 12,
        "minimum_order_value": "250.00",
        "purchasing_terms": {"payment": "Net 30", "freight": "FOB origin"},
    }
    body.update(overrides)
    return body


def audit_rows(db_session: Session, entity_id: uuid.UUID) -> list[AuditEvent]:
    """Vendor audit rows for one entity, ordered by action name.

    Not by ``occurred_at``: it defaults to ``now()``, the *transaction* start,
    and the test session wraps every request in one outer transaction, so
    every row shares a timestamp. In production each request is its own
    transaction. (The dev-token issuance writes its own row; only vendor
    actions count.)
    """
    return sorted(
        db_session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == entity_id, AuditEvent.action.like("vendor%")
            )
        )
        .scalars()
        .all(),
        key=lambda e: e.action,
    )


# --- authentication and authorization -------------------------------------------------


class TestAccessControl:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", VENDORS),
            ("POST", VENDORS),
            ("GET", f"{VENDORS}/{uuid.uuid4()}"),
            ("PATCH", f"{VENDORS}/{uuid.uuid4()}"),
            ("POST", f"{VENDORS}/{uuid.uuid4()}/deactivate"),
            ("GET", f"{VENDORS}/{uuid.uuid4()}/contacts"),
            ("POST", f"{VENDORS}/{uuid.uuid4()}/contacts"),
        ],
    )
    def test_every_route_requires_authentication(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, path, json={})

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    def test_a_viewer_can_read_but_not_write(self, client: TestClient, login: Login) -> None:
        headers, _ = login(RoleCode.VIEWER)

        assert client.get(VENDORS, headers=headers).status_code == 200
        forbidden = client.post(VENDORS, json=payload(), headers=headers)
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "forbidden"

    def test_a_data_operator_can_write_and_read(self, client: TestClient, login: Login) -> None:
        """The role model is flat: a writer must be granted reads explicitly.

        Found live: with reads gated on VIEWER alone, a DATA_OPERATOR could
        create a vendor and then get 403 fetching it.
        """
        headers, _ = login(RoleCode.DATA_OPERATOR)

        created = client.post(VENDORS, json=payload(), headers=headers)
        assert created.status_code == 201
        vendor_id = created.json()["id"]
        assert client.get(VENDORS, headers=headers).status_code == 200
        assert client.get(f"{VENDORS}/{vendor_id}", headers=headers).status_code == 200
        assert client.get(f"{VENDORS}/{vendor_id}/contacts", headers=headers).status_code == 200

    def test_every_role_can_read(self, client: TestClient, login: Login) -> None:
        for role in RoleCode:
            headers, _ = login(role)
            assert client.get(VENDORS, headers=headers).status_code == 200, role

    def test_a_purchasing_manager_cannot_write_vendors(
        self, client: TestClient, login: Login
    ) -> None:
        headers, _ = login(RoleCode.PURCHASING_MANAGER)

        assert client.post(VENDORS, json=payload(), headers=headers).status_code == 403

    def test_an_admin_passes_every_check(self, client: TestClient, login: Login) -> None:
        headers, _ = login(RoleCode.ADMIN)

        created = client.post(VENDORS, json=payload(), headers=headers)
        assert created.status_code == 201
        assert client.get(VENDORS, headers=headers).status_code == 200

    def test_a_forbidden_write_leaves_no_trace(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        headers, _ = login(RoleCode.VIEWER)

        client.post(VENDORS, json=payload(), headers=headers)

        assert db_session.execute(select(Vendor)).scalars().all() == []


# --- create / read / update -------------------------------------------------------------


class TestVendorLifecycle:
    def test_create_returns_the_vendor_with_every_field(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        headers, user = login(RoleCode.DATA_OPERATOR)

        response = client.post(VENDORS, json=payload(code="acme"), headers=headers)

        assert response.status_code == 201
        body = response.json()
        assert body["code"] == "ACME"
        assert body["name"] == "Acme Distribution"
        assert body["status"] == "ACTIVE"
        assert body["is_active"] is True
        assert body["minimum_order_quantity"] == 12
        assert body["minimum_order_value"] == "250.00"
        assert body["purchasing_terms"] == {"payment": "Net 30", "freight": "FOB origin"}
        assert body["contacts"] == []
        assert uuid.UUID(body["id"])

        [event] = audit_rows(db_session, uuid.UUID(body["id"]))
        assert event.action == "vendor.created"
        assert event.actor_type is ActorType.USER
        assert event.actor_user_id == user.id
        assert event.before is None
        assert event.after is not None and event.after["code"] == "ACME"
        assert event.request_id is not None

    def test_a_duplicate_code_is_a_409_with_a_typed_body(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        """AC-4.2: never a 500."""
        headers, _ = login(RoleCode.DATA_OPERATOR)
        client.post(VENDORS, json=payload(code="ACME"), headers=headers)

        response = client.post(VENDORS, json=payload(code="acme "), headers=headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "vendor_code_taken"
        assert len(db_session.execute(select(Vendor)).scalars().all()) == 1

    def test_the_same_code_may_exist_in_another_organization(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        other = factories.make_organization(db_session)
        factories.make_vendor(db_session, other, code="ACME")
        headers, _ = login(RoleCode.DATA_OPERATOR)

        assert client.post(VENDORS, json=payload(code="ACME"), headers=headers).status_code == 201

    def test_an_invalid_payload_is_a_422_with_field_detail(
        self, client: TestClient, login: Login
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.post(
            VENDORS,
            json=payload(code="a", currency="dollars", minimum_order_quantity=-1),
            headers=headers,
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        locations = {tuple(f["location"]) for f in error["details"]["fields"]}
        assert ("body", "code") in locations
        assert ("body", "currency") in locations
        assert ("body", "minimum_order_quantity") in locations

    def test_get_returns_detail_with_contacts(self, client: TestClient, login: Login) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        vendor_id = client.post(VENDORS, json=payload(), headers=headers).json()["id"]
        client.post(
            f"{VENDORS}/{vendor_id}/contacts",
            json={"name": "Ann", "email": "ann@acme.test", "is_primary": True},
            headers=headers,
        )

        response = client.get(f"{VENDORS}/{vendor_id}", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == "ACME"
        assert [c["email"] for c in body["contacts"]] == ["ann@acme.test"]

    def test_get_of_another_organizations_vendor_is_404(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        other = factories.make_organization(db_session)
        foreign = factories.make_vendor(db_session, other)
        headers, _ = login(RoleCode.VIEWER)

        response = client.get(f"{VENDORS}/{foreign.id}", headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "vendor_not_found"

    def test_patch_changes_only_the_given_fields_and_audits_the_diff(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        headers, user = login(RoleCode.DATA_OPERATOR)
        vendor_id = client.post(VENDORS, json=payload(), headers=headers).json()["id"]

        response = client.patch(
            f"{VENDORS}/{vendor_id}",
            json={"name": "Acme Wholesale", "minimum_order_quantity": 24, "status": "ON_HOLD"},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Acme Wholesale"
        assert body["minimum_order_quantity"] == 24
        assert body["status"] == "ON_HOLD"
        assert body["currency"] == "USD"  # untouched
        assert body["purchasing_terms"] == {"payment": "Net 30", "freight": "FOB origin"}

        _created, updated = audit_rows(db_session, uuid.UUID(vendor_id))
        assert updated.action == "vendor.updated"
        assert updated.actor_user_id == user.id
        assert updated.before is not None and updated.before["name"] == "Acme Distribution"
        assert updated.after is not None and updated.after["name"] == "Acme Wholesale"
        assert set(updated.changed_fields or []) == {"name", "minimum_order_quantity", "status"}

    def test_patch_with_nothing_to_change_writes_no_audit_row(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        vendor_id = client.post(VENDORS, json=payload(), headers=headers).json()["id"]

        assert client.patch(f"{VENDORS}/{vendor_id}", json={}, headers=headers).status_code == 200
        assert len(audit_rows(db_session, uuid.UUID(vendor_id))) == 1  # the create only

    def test_the_code_cannot_be_changed_by_patch(self, client: TestClient, login: Login) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        vendor_id = client.post(VENDORS, json=payload(), headers=headers).json()["id"]

        response = client.patch(f"{VENDORS}/{vendor_id}", json={"code": "NEW"}, headers=headers)

        # Unknown fields are ignored by the schema; the code is unchanged.
        assert response.status_code == 200
        assert response.json()["code"] == "ACME"


# --- list ------------------------------------------------------------------------------


class TestVendorList:
    def test_search_filter_and_pagination(
        self, client: TestClient, login: Login, db_session: Session, organization: Organization
    ) -> None:
        headers, _ = login(RoleCode.VIEWER)
        for code, name, status in (
            ("ACME", "Acme Distribution", VendorStatus.ACTIVE),
            ("BOLT", "Bolt Supply", VendorStatus.ACTIVE),
            ("CRATE", "Crate & Co", VendorStatus.ON_HOLD),
        ):
            factories.make_vendor(db_session, organization, code=code, name=name, status=status)
        factories.make_vendor(db_session, factories.make_organization(db_session), code="ACME2")

        everything = client.get(VENDORS, headers=headers).json()
        assert everything["total"] == 3  # the other organization's vendor is invisible
        assert [v["code"] for v in everything["items"]] == ["ACME", "BOLT", "CRATE"]

        searched = client.get(VENDORS, params={"q": "acm"}, headers=headers).json()
        assert [v["code"] for v in searched["items"]] == ["ACME"]

        by_status = client.get(VENDORS, params={"status": "ON_HOLD"}, headers=headers).json()
        assert [v["code"] for v in by_status["items"]] == ["CRATE"]

        page_two = client.get(VENDORS, params={"page": 2, "page_size": 2}, headers=headers).json()
        assert page_two == {
            "items": page_two["items"],
            "page": 2,
            "page_size": 2,
            "total": 3,
        }
        assert [v["code"] for v in page_two["items"]] == ["CRATE"]

    def test_bad_query_parameters_are_422(self, client: TestClient, login: Login) -> None:
        headers, _ = login(RoleCode.VIEWER)

        assert client.get(VENDORS, params={"page": 0}, headers=headers).status_code == 422
        assert client.get(VENDORS, params={"page_size": 500}, headers=headers).status_code == 422
        assert client.get(VENDORS, params={"status": "WEIRD"}, headers=headers).status_code == 422


# --- deactivate -------------------------------------------------------------------------


class TestDeactivate:
    def test_deactivation_keeps_the_row_and_audits(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        """AC-4.3: deactivated, never deleted."""
        headers, user = login(RoleCode.DATA_OPERATOR)
        vendor_id = client.post(VENDORS, json=payload(), headers=headers).json()["id"]

        response = client.post(f"{VENDORS}/{vendor_id}/deactivate", headers=headers)

        assert response.status_code == 200
        assert response.json()["is_active"] is False
        assert response.json()["status"] == "INACTIVE"
        row = db_session.get(Vendor, uuid.UUID(vendor_id))
        assert row is not None and row.is_active is False
        rows = audit_rows(db_session, uuid.UUID(vendor_id))
        event = next(e for e in rows if e.action == "vendor.deactivated")
        assert event.actor_user_id == user.id
        assert event.before is not None and event.before["is_active"] is True
        assert event.after is not None and event.after["is_active"] is False

    def test_deactivating_twice_is_a_409(self, client: TestClient, login: Login) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        vendor_id = client.post(VENDORS, json=payload(), headers=headers).json()["id"]
        client.post(f"{VENDORS}/{vendor_id}/deactivate", headers=headers)

        response = client.post(f"{VENDORS}/{vendor_id}/deactivate", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "vendor_already_inactive"

    def test_a_vendor_with_an_active_import_profile_cannot_be_deactivated(
        self, client: TestClient, login: Login, db_session: Session, organization: Organization
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        vendor = factories.make_vendor(db_session, organization, code="LIVE")
        factories.make_import_profile(db_session, organization, vendor)
        db_session.flush()

        response = client.post(f"{VENDORS}/{vendor.id}/deactivate", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "vendor_has_active_import_profiles"
        db_session.refresh(vendor)
        assert vendor.is_active is True
        assert audit_rows(db_session, vendor.id) == []

    def test_deactivating_after_the_profile_is_retired_succeeds(
        self, client: TestClient, login: Login, db_session: Session, organization: Organization
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        vendor = factories.make_vendor(db_session, organization, code="LIVE")
        profile = factories.make_import_profile(db_session, organization, vendor)
        profile.is_active = False
        db_session.flush()

        assert client.post(f"{VENDORS}/{vendor.id}/deactivate", headers=headers).status_code == 200


# --- contacts ---------------------------------------------------------------------------


class TestContacts:
    @pytest.fixture
    def vendor_id(self, client: TestClient, login: Login) -> str:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        return str(client.post(VENDORS, json=payload(), headers=headers).json()["id"])

    def test_add_update_list_and_deactivate_each_audited(
        self, client: TestClient, login: Login, db_session: Session, vendor_id: str
    ) -> None:
        headers, user = login(RoleCode.DATA_OPERATOR)
        base = f"{VENDORS}/{vendor_id}/contacts"

        added = client.post(
            base,
            json={"name": "Ann", "email": "ANN@acme.test", "role": "Sales", "is_primary": True},
            headers=headers,
        )
        assert added.status_code == 201
        contact = added.json()
        assert contact["email"] == "ann@acme.test"
        assert contact["is_primary"] is True
        contact_id = contact["id"]

        updated = client.patch(
            f"{base}/{contact_id}", json={"role": "Account manager"}, headers=headers
        )
        assert updated.status_code == 200
        assert updated.json()["role"] == "Account manager"

        listed = client.get(base, headers=headers)
        assert [c["id"] for c in listed.json()] == [contact_id]
        assert client.get(f"{base}/{contact_id}", headers=headers).status_code == 200

        deactivated = client.post(f"{base}/{contact_id}/deactivate", headers=headers)
        assert deactivated.status_code == 200
        assert deactivated.json()["is_active"] is False
        assert deactivated.json()["is_primary"] is False
        assert db_session.get(VendorContact, uuid.UUID(contact_id)) is not None

        events = audit_rows(db_session, uuid.UUID(contact_id))
        assert [e.action for e in events] == [  # sorted by action name
            "vendor_contact.created",
            "vendor_contact.deactivated",
            "vendor_contact.updated",
        ]
        assert all(e.actor_user_id == user.id for e in events)
        updated_event = events[2]
        assert updated_event.before is not None and updated_event.before["role"] == "Sales"
        assert updated_event.after is not None and updated_event.after["role"] == "Account manager"

    def test_duplicate_email_is_a_409_case_insensitively(
        self, client: TestClient, login: Login, vendor_id: str
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        base = f"{VENDORS}/{vendor_id}/contacts"
        client.post(base, json={"name": "Ann", "email": "ann@acme.test"}, headers=headers)

        response = client.post(
            base, json={"name": "Ann 2", "email": "Ann@Acme.Test"}, headers=headers
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "vendor_contact_email_taken"

    def test_only_one_primary_contact_and_the_old_one_steps_down(
        self, client: TestClient, login: Login, db_session: Session, vendor_id: str
    ) -> None:
        headers, _ = login(RoleCode.DATA_OPERATOR)
        base = f"{VENDORS}/{vendor_id}/contacts"
        first = client.post(
            base,
            json={"name": "Ann", "email": "ann@acme.test", "is_primary": True},
            headers=headers,
        ).json()

        second = client.post(
            base,
            json={"name": "Bob", "email": "bob@acme.test", "is_primary": True},
            headers=headers,
        ).json()

        contacts = {c["email"]: c for c in client.get(base, headers=headers).json()}
        assert contacts["bob@acme.test"]["is_primary"] is True
        assert contacts["ann@acme.test"]["is_primary"] is False
        # The demotion is its own audited change on Ann's row.
        assert [e.action for e in audit_rows(db_session, uuid.UUID(first["id"]))] == [
            "vendor_contact.created",
            "vendor_contact.updated",
        ]
        assert [e.action for e in audit_rows(db_session, uuid.UUID(second["id"]))] == [
            "vendor_contact.created"
        ]

    def test_a_viewer_cannot_add_a_contact(
        self, client: TestClient, login: Login, vendor_id: str
    ) -> None:
        headers, _ = login(RoleCode.VIEWER)

        response = client.post(
            f"{VENDORS}/{vendor_id}/contacts",
            json={"name": "Ann", "email": "ann@acme.test"},
            headers=headers,
        )

        assert response.status_code == 403

    def test_a_contact_on_another_organizations_vendor_is_404(
        self, client: TestClient, login: Login, db_session: Session
    ) -> None:
        other = factories.make_organization(db_session)
        foreign = factories.make_vendor(db_session, other)
        headers, _ = login(RoleCode.DATA_OPERATOR)

        response = client.post(
            f"{VENDORS}/{foreign.id}/contacts",
            json={"name": "Ann", "email": "ann@acme.test"},
            headers=headers,
        )

        assert response.status_code == 404

    def test_unknown_contact_is_404(self, client: TestClient, login: Login, vendor_id: str) -> None:
        headers, _ = login(RoleCode.VIEWER)

        response = client.get(f"{VENDORS}/{vendor_id}/contacts/{uuid.uuid4()}", headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "vendor_contact_not_found"


# --- the audit invariant, stated once for everything above ------------------------------


def test_every_mutation_produces_exactly_one_audit_row_with_before_and_after(
    client: TestClient, login: Login, db_session: Session
) -> None:
    """AC-4.4 and AC-12.2, as one end-to-end count."""
    headers, user = login(RoleCode.DATA_OPERATOR)
    vendor_id = client.post(VENDORS, json=payload(), headers=headers).json()["id"]
    client.patch(f"{VENDORS}/{vendor_id}", json={"name": "Renamed"}, headers=headers)
    contact_id = client.post(
        f"{VENDORS}/{vendor_id}/contacts",
        json={"name": "Ann", "email": "ann@acme.test"},
        headers=headers,
    ).json()["id"]
    client.patch(
        f"{VENDORS}/{vendor_id}/contacts/{contact_id}", json={"role": "Sales"}, headers=headers
    )
    client.post(f"{VENDORS}/{vendor_id}/contacts/{contact_id}/deactivate", headers=headers)
    client.post(f"{VENDORS}/{vendor_id}/deactivate", headers=headers)

    events = (
        db_session.execute(select(AuditEvent).where(AuditEvent.action.like("vendor%")))
        .scalars()
        .all()
    )

    # Compared as a sorted list: within the test's single outer transaction
    # every row shares the same occurred_at, so insertion order is not
    # recoverable — one row per mutation is what is being asserted.
    assert sorted(e.action for e in events) == sorted(
        [
            "vendor.created",
            "vendor.updated",
            "vendor_contact.created",
            "vendor_contact.updated",
            "vendor_contact.deactivated",
            "vendor.deactivated",
        ]
    )
    for event in events:
        assert event.actor_type is ActorType.USER
        assert event.actor_user_id == user.id
        assert event.entity_id is not None
        assert event.after is not None
        if event.action.endswith(".created"):
            assert event.before is None
        else:
            assert event.before is not None
            assert event.changed_fields  # something actually changed
