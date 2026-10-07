"""Read-only staging evidence: scoped counts, scheduled runs and raw checksums.

    python -m app.cli.staging_check --org-slug global-supplies

No credentials, filenames, raw payloads or user addresses are printed. This
does not certify persistent storage or browser sign-in; verify those separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.transaction import session_lifecycle
from app.imports.storage import StorageError, build_storage_backend
from app.models import Organization
from app.models.amazon import AmazonSyncRun
from app.models.catalog import MarketplaceListing, Product
from app.models.enums import AmazonSyncJobType, TriggerType
from app.models.ingestion import ImportFile
from app.models.sync import NineyardSyncRun
from app.repositories.scoping import TenantScope


def report(session: Session, settings: Settings, slug: str) -> dict[str, Any]:
    organization = session.scalar(
        select(Organization).where(Organization.slug == slug, Organization.is_active.is_(True))
    )
    if organization is None:
        raise ValueError("No active organization with that slug.")
    scope = TenantScope(organization.id)
    scheduled: dict[str, Any] = {}
    for job_type in AmazonSyncJobType:
        run = session.scalar(
            scope.select(AmazonSyncRun)
            .where(
                AmazonSyncRun.trigger_type == TriggerType.SCHEDULED,
                AmazonSyncRun.job_type == job_type,
            )
            .order_by(AmazonSyncRun.created_at.desc(), AmazonSyncRun.id.desc())
            .limit(1)
        )
        scheduled[job_type.value] = (
            None
            if run is None
            else {
                "status": run.status.value,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            }
        )
    nineyard = session.scalar(
        scope.select(NineyardSyncRun)
        .where(NineyardSyncRun.trigger_type == TriggerType.SCHEDULED)
        .order_by(NineyardSyncRun.created_at.desc(), NineyardSyncRun.id.desc())
        .limit(1)
    )
    scheduled["NINEYARD_CATALOG"] = (
        None
        if nineyard is None
        else {
            "status": nineyard.status.value,
            "started_at": nineyard.started_at,
            "completed_at": nineyard.completed_at,
        }
    )
    storage = build_storage_backend(settings)
    files = session.scalars(
        scope.select(ImportFile)
        .order_by(ImportFile.uploaded_at.desc(), ImportFile.id.desc())
        .limit(10)
    ).all()
    verified = 0
    failed: list[str] = []
    for file in files:
        try:
            content = storage.open(file.storage_uri)
            if (
                hashlib.sha256(content).hexdigest() == file.sha256
                and len(content) == file.size_bytes
            ):
                verified += 1
            else:
                failed.append(str(file.id))
        except (StorageError, OSError):
            failed.append(str(file.id))
    return {
        "checked_at": datetime.now(UTC),
        "organization": slug,
        "auth_backend": settings.auth_backend,
        "dev_auth_enabled": settings.dev_auth_enabled,
        "products": session.scalar(scope.select(Product, func.count()).select_from(Product)),
        "listings": session.scalar(
            scope.select(MarketplaceListing, func.count()).select_from(MarketplaceListing)
        ),
        "scheduler_config": {
            "amazon_enabled": settings.amazon_enabled,
            "nineyard_enabled": settings.nineyard_enabled,
            "amazon_organization_slug": settings.amazon_organization_slug,
            "nineyard_organization_slug": settings.nineyard_organization_slug,
            "orders_minutes": settings.amazon_orders_interval_minutes,
            "inventory_minutes": settings.amazon_inventory_interval_minutes,
            "listings_minutes": settings.amazon_listings_interval_minutes,
            "nineyard_minutes": settings.nineyard_sync_interval_minutes,
        },
        "latest_scheduled_runs": scheduled,
        "raw_files": {"checked": len(files), "verified": verified, "failed_ids": failed},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-slug", required=True)
    args = parser.parse_args()
    try:
        with session_lifecycle() as session:
            result = report(session, get_settings(), args.org_slug)
        print(json.dumps(result, default=str, indent=2))
        return 1 if result["raw_files"]["failed_ids"] else 0
    except Exception as exc:
        # Database exceptions can include connection details; never dump them.
        print(f"Staging check could not complete ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
