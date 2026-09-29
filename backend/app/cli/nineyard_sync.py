"""Run the read-only Nineyard catalog synchronization manually."""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.integrations.nineyard import NineyardCatalogReader, NineyardClient, NineyardConfig
from app.models.enums import SyncStatus, TriggerType
from app.services.amazon_runs import resolve_amazon_organization
from app.services.nineyard_sync import amazon_listing_skus, run_nineyard_sync


def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    session = get_session_factory()()
    try:
        organization_id = resolve_amazon_organization(session, settings.nineyard_organization_slug)
        seller_skus = amazon_listing_skus(session, organization_id)
        if not seller_skus:
            raise RuntimeError(
                "No active Amazon listings are available; run "
                "'python -m app.cli.amazon_poc sync-listings' first"
            )
        with NineyardClient(NineyardConfig.from_settings(settings)) as client:
            client.authenticate()
            run = run_nineyard_sync(
                session,
                organization_id,
                NineyardCatalogReader(
                    client,
                    sku_account=settings.nineyard_account,
                    seller_skus=seller_skus,
                ),
                TriggerType.MANUAL,
            )
        print(
            f"Nineyard catalog: {run.status.value} — seen {run.items_seen}, "
            f"created {run.items_created}, updated {run.items_updated}, "
            f"unchanged {run.items_unchanged}, removed {run.items_removed}, "
            f"failed {run.items_failed}; targeted {len(seller_skus)} Amazon SKUs, "
            f"mappings seen {run.error_details.get('sku_mappings_seen', 0)}, "
            f"identifiers created {run.error_details.get('sku_identifiers_created', 0)}"
        )
        return 0 if run.status in (SyncStatus.COMPLETED, SyncStatus.COMPLETED_WITH_ERRORS) else 1
    except Exception as exc:
        print(f"Nineyard catalog: FAILED — {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
