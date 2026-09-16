"""EXPLAIN ANALYZE the queries the admin interface leans on (phase 11).

    python -m app.cli.explain_queries --org-slug demo

Builds the *real* statements — the same repository code the routes call —
for one organization and prints PostgreSQL's plan for each, flagging any
sequential scan over a table with more than ``--seq-scan-threshold`` rows.
Read-only: EXPLAIN ANALYZE executes the SELECTs and nothing else.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.transaction import session_scope
from app.models import Organization
from app.models.catalog import Product
from app.models.enums import ExceptionStatus, ImportRowStatus
from app.models.ingestion import ImportJob, ImportJobRow
from app.models.inventory import AvailabilityEvent
from app.models.matching import ProductMappingException
from app.repositories.scoping import ScopedRepository


class _Repository(ScopedRepository):
    pass


def statements(session: Session, organization_id: uuid.UUID) -> dict[str, Select[Any]]:
    repository = _Repository(session, organization_id)
    now = datetime.now(UTC)
    latest_job = session.execute(
        repository.select(ImportJob).order_by(ImportJob.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    job_id = latest_job.id if latest_job else uuid.uuid4()
    score = func.similarity(Product.name, "Blue Widget 12 pack")
    return {
        "exceptions list (queue view)": (
            repository.select(ProductMappingException)
            .where(
                ProductMappingException.status == ExceptionStatus.PENDING,
                (ProductMappingException.deferred_until.is_(None))
                | (ProductMappingException.deferred_until <= now),
            )
            .order_by(ProductMappingException.created_at, ProductMappingException.id)
            .limit(50)
        ),
        "availability feed": (
            repository.select(AvailabilityEvent)
            .where(AvailabilityEvent.detected_at >= now - timedelta(days=30))
            .order_by(AvailabilityEvent.detected_at.desc(), AvailabilityEvent.id)
            .limit(50)
        ),
        "product search (trigram similarity, rule 5)": (
            repository.select(Product, Product.id, score)
            .where(Product.is_active.is_(True), score >= 0.3)
            .order_by(score.desc(), Product.id)
            .limit(5)
        ),
        "product lookup by catalog number": (
            repository.select(Product).where(Product.catalog_item_number == "DEMO-001")
        ),
        "import rows by job and status": (
            repository.select(ImportJobRow)
            .where(
                ImportJobRow.import_job_id == job_id,
                ImportJobRow.status == ImportRowStatus.ERROR,
            )
            .order_by(ImportJobRow.row_number)
            .limit(50)
        ),
        "import job list": (
            repository.select(ImportJob)
            .order_by(ImportJob.created_at.desc(), ImportJob.id)
            .limit(25)
        ),
    }


def explain(session: Session, statement: Select[Any]) -> list[str]:
    # Compile against the session's own dialect so enums and UUIDs render as
    # PostgreSQL literals.
    bind = session.get_bind()
    compiled = str(statement.compile(dialect=bind.dialect, compile_kwargs={"literal_binds": True}))
    rows = session.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {compiled}")).scalars().all()
    return [str(row) for row in rows]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-slug", default="demo")
    parser.add_argument("--seq-scan-threshold", type=int, default=10_000)
    arguments = parser.parse_args(argv)
    if get_settings().is_production:
        print("explain_queries is a development tool", file=sys.stderr)
        return 2

    problems = 0
    with session_scope() as session:
        organization = session.execute(
            select(Organization).where(Organization.slug == arguments.org_slug)
        ).scalar_one_or_none()
        if organization is None:
            print(f"no organization {arguments.org_slug!r}", file=sys.stderr)
            return 2
        for name, statement in statements(session, organization.id).items():
            plan = explain(session, statement)
            print(f"\n== {name}")
            for line in plan:
                print("  " + line)
            for line in plan:
                if "Seq Scan on" in line and "rows=" in line:
                    # "Seq Scan on t  (cost=... rows=N ...) (actual ... rows=M ...)"
                    estimate = line.split("rows=")[1].split()[0]
                    if estimate.isdigit() and int(estimate) > arguments.seq_scan_threshold:
                        problems += 1
                        print(f"  !! sequential scan over ~{estimate} rows")
    print(f"\n{problems} large sequential scan(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
