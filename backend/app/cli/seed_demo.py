"""Seed a demonstration organization: two vendors with materially different
files, their import profiles, fixture imports run through the whole
lifecycle, and a watchlist entry that an import flips to in stock.

    python -m app.cli.seed_demo

For client demos and CI. Builds on ``seed_dev`` (organization, users, the
DEMO-001 product) and adds:

* four more products with UPCs;
* **NORTHWIND** — a CSV vendor, profile ``UPC | Description | Quantity | Cost``;
* **CONTOSO** — an XLSX vendor, profile
  ``Vendor SKU | UPC | Qty Available | Wholesale Price``;
* a watch on "Blue Widget 12 pack" at any vendor;
* three imports: Northwind Monday (Blue Widget at 0), Contoso Monday
  (identifiers plus one row only a person can settle), Northwind Tuesday
  (Blue Widget at 40 — the "now available from Vendor B" event).

Idempotent by construction: vendors and profiles are found before they are
created, and identical file bytes are a duplicate upload, so a second run
changes nothing. Refused unless ``DEV_AUTH_ENABLED`` is true.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import re
import sys
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli.seed_dev import seed as seed_dev
from app.core.config import Settings, get_settings
from app.core.security import Principal, RoleCode
from app.db.transaction import session_scope
from app.imports.storage import StorageBackend, build_storage_backend
from app.models import Organization, User
from app.models.catalog import Product, ProductIdentifier
from app.models.enums import (
    IdentifierType,
    ImportJobStage,
    ImportJobStatus,
    SourceSystem,
)
from app.models.vendor import Vendor
from app.schemas.import_profiles import ImportProfileCreate
from app.schemas.watchlist import WatchlistCreate
from app.services import import_profiles, imports, watchlist
from app.services.import_processing import process_import_job
from app.services.matching import match_import_job
from app.services.snapshots import snapshot_import_job

PRODUCTS: tuple[tuple[str, str, str], ...] = (
    # catalog item number, name, UPC-A (12 digits, valid check digit)
    ("DEMO-002", "Red Widget 6 pack", "036000291452"),
    ("DEMO-003", "Green Gadget", "042100005264"),
    ("DEMO-004", "Yellow Gizmo XL", "012000000010"),
    ("DEMO-005", "Black Bracket", "054000004138"),
)

NORTHWIND_HEADERS = ["UPC", "Description", "Quantity", "Cost"]
CONTOSO_HEADERS = ["Vendor SKU", "UPC", "Qty Available", "Wholesale Price"]


@dataclass
class DemoSummary:
    vendors: list[str]
    jobs: list[tuple[str, str, str]]  # vendor, file, status
    watch_status: str | None


def dev_principal(session: Session, organization: Organization) -> Principal:
    admin = (
        session.execute(
            select(User)
            .where(User.organization_id == organization.id)
            .order_by(User.created_at, User.email)
        )
        .scalars()
        .first()
    )
    if admin is None:
        raise RuntimeError("seed_dev must run first: no user in the organization")
    return Principal(
        user_id=admin.id,
        organization_id=organization.id,
        email=admin.email,
        display_name=admin.display_name,
        roles=frozenset({RoleCode.ADMIN}),
    )


def csv_bytes(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def xlsx_bytes(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    # Pin the document timestamps so the bytes are identical from run to run:
    # the second run must be a duplicate upload, not a new import.
    workbook.properties.created = datetime(2026, 9, 16, tzinfo=UTC)
    workbook.properties.modified = datetime(2026, 9, 16, tzinfo=UTC)
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Price List"
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _stable_zip(buffer.getvalue())


def _stable_zip(content: bytes) -> bytes:
    """Rewrite a zip with fixed entry timestamps. openpyxl stamps every entry
    with the wall clock, so two builds seconds apart differ in bytes — and
    the import's checksum dedupe would see two files."""
    source = zipfile.ZipFile(io.BytesIO(content))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "docProps/core.xml":
                # openpyxl stamps `modified` with the wall clock on every save.
                data = re.sub(
                    rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                    lambda m: m.group(1) + b"2026-09-16T00:00:00Z" + m.group(2),
                    data,
                )
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 16, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, data)
    return buffer.getvalue()


def _ensure_products(session: Session, organization: Organization) -> None:
    for number, name, upc in PRODUCTS:
        product = session.execute(
            select(Product).where(
                Product.organization_id == organization.id, Product.catalog_item_number == number
            )
        ).scalar_one_or_none()
        if product is None:
            product = Product(
                organization_id=organization.id, catalog_item_number=number, name=name, brand="Demo"
            )
            session.add(product)
            session.flush()
        canonical = upc.zfill(14)
        if (
            session.execute(
                select(ProductIdentifier).where(
                    ProductIdentifier.organization_id == organization.id,
                    ProductIdentifier.identifier_type == IdentifierType.UPC,
                    ProductIdentifier.normalized_value == canonical,
                )
            ).scalar_one_or_none()
            is None
        ):
            session.add(
                ProductIdentifier(
                    organization_id=organization.id,
                    product_id=product.id,
                    identifier_type=IdentifierType.UPC,
                    raw_value=upc,
                    normalized_value=canonical,
                    source_system=SourceSystem.MANUAL,
                    has_valid_checksum=True,
                    is_primary=True,
                )
            )
    session.flush()


def _ensure_vendor(
    session: Session, organization: Organization, code: str, name: str, **fields: Any
) -> Vendor:
    vendor = session.execute(
        select(Vendor).where(Vendor.organization_id == organization.id, Vendor.code == code)
    ).scalar_one_or_none()
    if vendor is None:
        vendor = Vendor(organization_id=organization.id, code=code, name=name, **fields)
        session.add(vendor)
        session.flush()
    return vendor


def _ensure_profile(
    session: Session, actor: Principal, vendor: Vendor, payload: dict[str, Any]
) -> None:
    if import_profiles.list_profiles(session, actor, vendor.id, include_inactive=False):
        return
    import_profiles.create_profile(
        session, actor, vendor.id, ImportProfileCreate.model_validate(payload)
    )


def _import(
    session: Session,
    storage: StorageBackend,
    actor: Principal,
    vendor: Vendor,
    filename: str,
    content: bytes,
    max_bytes: int,
) -> tuple[str, str, str]:
    profile = import_profiles.list_profiles(session, actor, vendor.id, include_inactive=False)[0]
    outcome = imports.receive_upload(
        session,
        storage,
        principal=actor,
        vendor_id=vendor.id,
        profile_id=profile.id,
        filename=filename,
        content=content,
        declared_mime=None,
        max_bytes=max_bytes,
    )
    job = outcome.job
    if outcome.duplicate:
        return vendor.code, filename, f"{job.status.value} (already imported)"
    job = process_import_job(
        session, storage, organization_id=actor.organization_id, job_id=job.id, actor=actor
    )
    if job.status is ImportJobStatus.RUNNING and job.current_stage is ImportJobStage.MATCHING:
        job = match_import_job(
            session, organization_id=actor.organization_id, job_id=job.id, actor=actor
        )
    if job.status is ImportJobStatus.RUNNING and job.current_stage is ImportJobStage.SNAPSHOTTING:
        job = snapshot_import_job(
            session, organization_id=actor.organization_id, job_id=job.id, actor=actor
        )
    return vendor.code, filename, job.status.value


def seed(session: Session, storage: StorageBackend, *, max_bytes: int) -> DemoSummary:
    seed_dev(session, org_slug="demo", org_name="Demo Distribution", domain="example.test")
    organization = session.execute(
        select(Organization).where(Organization.slug == "demo")
    ).scalar_one()
    actor = dev_principal(session, organization)
    _ensure_products(session, organization)

    northwind = _ensure_vendor(
        session,
        organization,
        "NORTHWIND",
        "Northwind Traders",
        contact_email="orders@northwind.example",
        minimum_order_quantity=12,
    )
    contoso = _ensure_vendor(
        session,
        organization,
        "CONTOSO",
        "Contoso Wholesale",
        contact_email="sales@contoso.example",
        minimum_order_value=Decimal("250.00"),
    )
    _ensure_profile(
        session,
        actor,
        northwind,
        {
            "name": "weekly-price-list",
            "file_format": "CSV",
            "column_map": {
                "columns": [
                    {"target": "upc", "source": "UPC"},
                    {"target": "description", "source": "Description"},
                    {"target": "quantity_available", "source": "Quantity"},
                    {"target": "unit_cost", "source": "Cost"},
                ]
            },
            "normalization_rules": {"currency_symbols_to_strip": ["$"]},
        },
    )
    _ensure_profile(
        session,
        actor,
        contoso,
        {
            "name": "wholesale-workbook",
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
        },
    )

    # Watch "Blue Widget 12 pack" (DEMO-001, UPC 012345678905) at any vendor.
    blue = session.execute(
        select(Product).where(
            Product.organization_id == organization.id, Product.catalog_item_number == "DEMO-001"
        )
    ).scalar_one()
    with contextlib.suppress(watchlist.WatchAlreadyActive):
        watchlist.add_watch(
            session,
            actor,
            WatchlistCreate(
                product_id=blue.id,
                reason="Best seller, out everywhere",
                desired_quantity=Decimal("24"),
                max_unit_cost=Decimal("5.00"),
            ),
        )

    jobs = [
        _import(
            session,
            storage,
            actor,
            northwind,
            "northwind-monday.csv",
            csv_bytes(
                NORTHWIND_HEADERS,
                [
                    ["012345678905", "Blue Widget 12 pack", "0", "$4.50"],
                    ["036000291452", "Red Widget 6 pack", "18", "$2.10"],
                    ["042100005264", "Green Gadget", "7", "$9.99"],
                    ["012345678906", "Bad check digit widget", "3", "$1.00"],
                ],
            ),
            max_bytes,
        ),
        _import(
            session,
            storage,
            actor,
            contoso,
            "contoso-monday.xlsx",
            xlsx_bytes(
                CONTOSO_HEADERS,
                [
                    ["CON-001", 12345678905, 100, 4.25],
                    ["CON-002", "054000004138", 0, 7.0],
                    ["CON-003", None, 3, 0.99],  # no UPC: the queue asks
                    ["CON-004", "012000000010", 12, 15.5],
                ],
            ),
            max_bytes,
        ),
        _import(
            session,
            storage,
            actor,
            northwind,
            "northwind-tuesday.csv",
            csv_bytes(
                NORTHWIND_HEADERS,
                [
                    ["012345678905", "Blue Widget 12 pack", "40", "$4.50"],
                    ["036000291452", "Red Widget 6 pack", "0", "$2.10"],
                    ["042100005264", "Green Gadget", "7", "$9.99"],
                ],
            ),
            max_bytes,
        ),
    ]
    entry = watchlist.list_watchlist(
        session,
        actor,
        page=1,
        page_size=1,
        vendor_id=None,
        product_id=blue.id,
        include_inactive=False,
    ).items
    return DemoSummary(
        vendors=[northwind.code, contoso.code],
        jobs=jobs,
        watch_status=entry[0].current_status.value if entry else None,
    )


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    settings = settings or get_settings()
    if settings.is_production or not settings.dev_auth_enabled:
        print("seed_demo is only for development: DEV_AUTH_ENABLED must be true", file=sys.stderr)
        return 2
    storage = build_storage_backend(settings)
    with session_scope() as session:
        summary = seed(session, storage, max_bytes=settings.import_max_upload_mb * 1024 * 1024)
    print(f"vendors: {', '.join(summary.vendors)}")
    for vendor, filename, status in summary.jobs:
        print(f"  {vendor:<10} {filename:<24} {status}")
    print(f"watch on DEMO-001: {summary.watch_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
