"""The reads the admin interface needs (phase 10): the audit feed, the
dashboard counts, and the development seed."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli.seed_dev import seed
from app.core.config import Settings
from app.core.security import RoleCode
from app.db.session import get_db
from app.main import create_app
from app.models import Organization, User
from app.models.catalog import Product
from app.models.enums import ExceptionStatus, ImportJobStatus, OosStatus
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


Headers = dict[str, str]
Login = Callable[[RoleCode], tuple[Headers, User]]


@pytest.fixture
def login(client: TestClient, db_session: Session, organization: Organization) -> Login:
    roles = ensure_system_roles(db_session, organization.id)

    def _login(role: RoleCode) -> tuple[Headers, User]:
        user = factories.make_user(db_session, organization, email=f"{role.value.lower()}@x.test")
        assign_role(db_session, user=user, role=roles[role])
        db_session.flush()
        token = client.post("/api/v1/auth/dev-token", json={"email": user.email}).json()[
            "access_token"
        ]
        return {"Authorization": f"Bearer {token}"}, user

    return _login


class TestAuditFeed:
    def test_filters_and_shape(
        self, client: TestClient, login: Login, db_session: Session, organization: Organization
    ) -> None:
        headers, _ = login(RoleCode.VIEWER)
        operator, op_user = login(RoleCode.DATA_OPERATOR)
        created = client.post(
            "/api/v1/vendors",
            json={"code": "ACME", "name": "Acme", "currency": "USD"},
            headers=operator,
        )
        assert created.status_code == 201, created.text
        vendor_id = created.json()["id"]
        client.patch(f"/api/v1/vendors/{vendor_id}", json={"name": "Acme Inc"}, headers=operator)

        feed = client.get("/api/v1/audit/events", headers=headers).json()
        assert feed["total"] >= 2
        assert "vendors" in feed["entity_types"]
        assert {"vendor.created", "vendor.updated"} <= set(feed["actions"])
        by_entity = client.get(
            "/api/v1/audit/events", params={"entity_id": vendor_id}, headers=headers
        ).json()
        assert {e["action"] for e in by_entity["items"]} == {"vendor.updated", "vendor.created"}
        updated = next(e for e in by_entity["items"] if e["action"] == "vendor.updated")
        assert updated["before"]["name"] == "Acme" and updated["after"]["name"] == "Acme Inc"
        assert "name" in updated["changed_fields"]
        assert updated["actor_user_id"] == str(op_user.id) and updated["actor_type"] == "USER"
        prefix = client.get(
            "/api/v1/audit/events", params={"action": "vendor.cre"}, headers=headers
        ).json()
        assert {e["action"] for e in prefix["items"]} == {"vendor.created"}
        by_actor = client.get(
            "/api/v1/audit/events", params={"actor": op_user.email}, headers=headers
        ).json()
        assert by_actor["total"] >= 2
        nobody = client.get(
            "/api/v1/audit/events", params={"actor": "nobody@x"}, headers=headers
        ).json()
        assert nobody["total"] == 0
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        later = client.get(
            "/api/v1/audit/events", params={"occurred_from": future}, headers=headers
        ).json()
        assert later["total"] == 0
        assert client.get("/api/v1/audit/events").status_code == 401
        other = factories.make_organization(db_session)
        factories.make_vendor(db_session, other, code="F")
        none = client.get(
            "/api/v1/audit/events", params={"entity_type": "nope"}, headers=headers
        ).json()
        assert none["total"] == 0


class TestDashboard:
    def test_counts_are_live(
        self, client: TestClient, login: Login, db_session: Session, organization: Organization
    ) -> None:
        headers, _ = login(RoleCode.VIEWER)
        vendor = factories.make_vendor(db_session, organization, code="ACME", name="Acme")
        quiet = factories.make_vendor(db_session, organization, code="QUIET", name="Quiet")
        product = factories.make_product(db_session, organization)
        factories.make_mapping_exception(db_session, organization, vendor)
        reviewer = factories.make_user(db_session, organization, email="reviewer@x.test")
        factories.make_mapping_exception(
            db_session,
            organization,
            vendor,
            status=ExceptionStatus.REJECTED,
            resolved_by_user_id=reviewer.id,
            resolved_at=datetime.now(UTC),
        )
        deferred = factories.make_mapping_exception(db_session, organization, vendor)
        deferred.deferred_until = datetime.now(UTC) + timedelta(days=1)
        entry = factories.make_watchlist_entry(
            db_session, organization, product, current_status=OosStatus.IN_STOCK
        )
        line = factories.make_vendor_product(db_session, organization, vendor)
        file = factories.make_import_file(db_session, organization, vendor)
        job = factories.make_import_job(
            db_session,
            organization,
            vendor,
            file,
            status=ImportJobStatus.COMPLETED,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            total_rows=5,
            exception_rows=1,
        )
        snapshot = factories.make_snapshot(db_session, organization, vendor, line, job)
        factories.make_availability_event(
            db_session,
            organization,
            vendor,
            line,
            snapshot,
            oos_watchlist_id=entry.id,
            product_id=product.id,
        )
        old_line = factories.make_vendor_product(db_session, organization, vendor)
        old_snapshot = factories.make_snapshot(db_session, organization, vendor, old_line, job)
        factories.make_availability_event(
            db_session,
            organization,
            vendor,
            old_line,
            old_snapshot,
            oos_watchlist_id=entry.id,
            detected_at=datetime.now(UTC) - timedelta(days=10),
        )
        db_session.flush()

        body = client.get("/api/v1/dashboard", headers=headers).json()

        assert body["open_exceptions"] == 1  # deferred and rejected excluded
        assert body["watchlist_active"] == 1 and body["watchlist_in_stock"] == 1
        assert body["watchlist_newly_available_7d"] == 1
        assert body["imports_running"] == 0
        last = {v["vendor_code"]: v for v in body["last_imports"]}
        assert last["ACME"]["status"] == "COMPLETED" and last["ACME"]["total_rows"] == 5
        assert last["ACME"]["job_id"] == str(job.id)
        assert last["QUIET"]["job_id"] is None and last["QUIET"]["vendor_id"] == str(quiet.id)
        assert body["amazon_syncs"] == [] and body["nineyard_sync"] is None
        assert client.get("/api/v1/dashboard").status_code == 401

    def test_sync_summaries(
        self, client: TestClient, login: Login, db_session: Session, organization: Organization
    ) -> None:
        headers, _ = login(RoleCode.VIEWER)
        factories.make_amazon_sync_run(db_session, organization)
        db_session.flush()

        body = client.get("/api/v1/dashboard", headers=headers).json()

        assert len(body["amazon_syncs"]) == 1
        assert body["amazon_syncs"][0]["status"] is not None


class TestSeed:
    def test_seed_is_idempotent_and_creates_the_four_roles(self, db_session: Session) -> None:
        first = seed(db_session, org_slug="e2e", org_name="E2E", domain="e2e.test")
        second = seed(db_session, org_slug="e2e", org_name="E2E", domain="e2e.test")

        assert (
            first
            == second
            == ["admin@e2e.test", "buyer@e2e.test", "operator@e2e.test", "viewer@e2e.test"]
        )
        organizations = (
            db_session.execute(select(Organization).where(Organization.slug == "e2e"))
            .scalars()
            .all()
        )
        assert len(organizations) == 1
        users = (
            db_session.execute(select(User).where(User.organization_id == organizations[0].id))
            .scalars()
            .all()
        )
        assert len(users) == 4
        assert all(u.user_roles for u in users)
        products = (
            db_session.execute(
                select(Product).where(Product.organization_id == organizations[0].id)
            )
            .scalars()
            .all()
        )
        assert [p.catalog_item_number for p in products] == ["DEMO-001"]


class TestSeedDemo:
    def test_the_demo_seed_runs_the_whole_lifecycle_and_is_idempotent(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        from app.cli.seed_demo import seed as seed_demo
        from app.imports.storage import LocalStorageBackend

        storage = LocalStorageBackend(tmp_path / "raw")
        first = seed_demo(db_session, storage, max_bytes=50 * 1024 * 1024)

        assert first.vendors == ["NORTHWIND", "CONTOSO"]
        assert [status for _, _, status in first.jobs] == [
            "COMPLETED",
            "COMPLETED",
            "COMPLETED",
        ]
        assert first.watch_status == "IN_STOCK"
        organization = db_session.execute(
            select(Organization).where(Organization.slug == "demo")
        ).scalar_one()
        products = (
            db_session.execute(select(Product).where(Product.organization_id == organization.id))
            .scalars()
            .all()
        )
        assert len(products) == 5

        second = seed_demo(db_session, storage, max_bytes=50 * 1024 * 1024)

        assert all("already imported" in status for _, _, status in second.jobs)
        assert second.watch_status == "IN_STOCK"
        assert (
            len(
                db_session.execute(
                    select(Product).where(Product.organization_id == organization.id)
                )
                .scalars()
                .all()
            )
            == 5
        )
