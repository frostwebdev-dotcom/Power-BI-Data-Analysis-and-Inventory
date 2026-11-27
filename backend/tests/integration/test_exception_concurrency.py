"""Concurrent decisions on one mapping exception (CLAUDE.md §5.2, AC-8.6).

Two people acting on the same queue item at once must not both succeed: the
second would otherwise overwrite the first approval without the supersession
step, and the job's counters would move twice. The decision takes a row lock
on the item, so the second caller waits and then finds it no longer PENDING.

These tests need independent connections, so their fixtures are committed in
a disposable database rather than the rolled-back ``db_session``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from app.core.security import Principal, RoleCode, load_principal
from app.models.enums import ExceptionStatus, ImportJobStatus, MappingStatus, MatchResult
from app.models.ingestion import ImportJob
from app.models.matching import ProductMappingException
from app.models.vendor import VendorProduct
from app.schemas.exceptions import ApproveRequest
from app.services import exceptions as service
from app.services.roles import assign_role, ensure_system_roles
from tests.integration import factories
from tests.integration.conftest import create_database, drop_database, run_migrations

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def isolated_engine(migrated_engine: Engine) -> Iterator[Engine]:
    url = migrated_engine.url.set(database=f"prms_exceptions_{uuid.uuid4().hex[:12]}_test")
    create_database(url)
    engine = create_engine(url)
    try:
        run_migrations(url)
        yield engine
    finally:
        engine.dispose()
        drop_database(url)


@dataclass(frozen=True)
class Fixture:
    manager: Principal
    exception_id: uuid.UUID
    line_id: uuid.UUID
    job_id: uuid.UUID
    first: uuid.UUID
    second: uuid.UUID


def committed_exception(engine: Engine) -> Fixture:
    with Session(engine, expire_on_commit=False) as session:
        organization = factories.make_organization(session)
        roles = ensure_system_roles(session, organization.id)
        user = factories.make_user(session, organization)
        assign_role(session, user=user, role=roles[RoleCode.PURCHASING_MANAGER])
        vendor = factories.make_vendor(session, organization)
        first = factories.make_product(session, organization, name="First choice")
        second = factories.make_product(session, organization, name="Second choice")
        line = factories.make_vendor_product(
            session, organization, vendor, mapping_status=MappingStatus.UNMAPPED
        )
        job = factories.make_import_job(
            session,
            organization,
            vendor,
            factories.make_import_file(session, organization, vendor),
            status=ImportJobStatus.RUNNING,
            exception_rows=1,
            matched_rows=0,
        )
        row = factories.make_import_job_row(
            session,
            organization,
            job,
            vendor_sku=line.vendor_sku,
            normalized_vendor_sku=line.normalized_vendor_sku,
            vendor_product_id=line.id,
            match_result=MatchResult.UNMATCHED,
        )
        item = factories.make_mapping_exception(
            session,
            organization,
            vendor,
            vendor_product_id=line.id,
            import_job_id=job.id,
            import_job_row_id=row.id,
            status=ExceptionStatus.PENDING,
        )
        session.commit()
        manager = load_principal(session, user.id)
        return Fixture(manager, item.id, line.id, job.id, first.id, second.id)


def approve(engine: Engine, fixture: Fixture, product_id: uuid.UUID) -> service.Resolution:
    with Session(engine) as session:
        return service.approve(
            session, fixture.manager, fixture.exception_id, ApproveRequest(product_id=product_id)
        )


def test_a_decision_holds_the_item_until_it_commits(isolated_engine: Engine) -> None:
    fixture = committed_exception(isolated_engine)

    with Session(isolated_engine) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        # The first caller has read and locked the item but not yet committed.
        service._pending(holder, fixture.manager, fixture.exception_id)
        competing = pool.submit(approve, isolated_engine, fixture, fixture.second)
        time.sleep(0.5)
        assert not competing.done(), "the competing approval did not wait for the lock"

        # The first decision commits; only then may the second proceed — and
        # it must re-read the item, find it APPROVED, and refuse.
        service.approve(
            holder,
            fixture.manager,
            fixture.exception_id,
            ApproveRequest(product_id=fixture.first),
        )
        with pytest.raises(service.ExceptionNotPending):
            competing.result(timeout=30)

    with Session(isolated_engine) as session:
        item = session.get(ProductMappingException, fixture.exception_id)
        line = session.get(VendorProduct, fixture.line_id)
        job = session.get(ImportJob, fixture.job_id)
        assert item is not None and line is not None and job is not None
        assert item.status is ExceptionStatus.APPROVED
        assert item.resolved_product_id == fixture.first
        assert line.mapping_status is MappingStatus.APPROVED
        assert line.product_id == fixture.first
        # The row moved from exceptions to matches exactly once.
        assert (job.matched_rows, job.exception_rows) == (1, 0)


def test_concurrent_approvals_have_exactly_one_winner(isolated_engine: Engine) -> None:
    fixture = committed_exception(isolated_engine)

    def attempt(product_id: uuid.UUID) -> str:
        try:
            approve(isolated_engine, fixture, product_id)
            return "approved"
        except service.ExceptionNotPending:
            return "not_pending"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(attempt, [fixture.first, fixture.second]))
    assert outcomes == ["approved", "not_pending"]

    with Session(isolated_engine) as session:
        item = session.get(ProductMappingException, fixture.exception_id)
        line = session.get(VendorProduct, fixture.line_id)
        job = session.get(ImportJob, fixture.job_id)
        assert item is not None and line is not None and job is not None
        assert line.product_id == item.resolved_product_id
        assert (job.matched_rows, job.exception_rows) == (1, 0)
        approvals = session.scalars(
            select(ProductMappingException).where(
                ProductMappingException.id == fixture.exception_id,
                ProductMappingException.status == ExceptionStatus.APPROVED,
            )
        ).all()
        assert len(approvals) == 1
