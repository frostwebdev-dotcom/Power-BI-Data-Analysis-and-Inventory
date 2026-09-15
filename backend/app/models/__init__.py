"""SQLAlchemy ORM models.

Importing this package registers every mapped class on ``Base.metadata``, which
is what lets Alembic autogenerate see the full schema. Import it — never a
submodule — anywhere the complete metadata is needed.

Conventions every model follows (CLAUDE.md §4, ADR 0002, ADR 0009):

* an immutable internal UUID primary key; business keys are separate, uniquely
  constrained columns and are never foreign-key targets
* ``TIMESTAMPTZ`` columns written in UTC
* ``organization_id`` on every organization-owned table
"""

from __future__ import annotations

from app.models.amazon import AmazonInventorySnapshot, AmazonOrderLine, AmazonSyncRun
from app.models.audit import AuditEvent
from app.models.catalog import MarketplaceListing, Product, ProductIdentifier
from app.models.identity import Role, User, UserRole
from app.models.ingestion import ImportFile, ImportJob, ImportJobRow
from app.models.inventory import AvailabilityEvent, VendorInventorySnapshot
from app.models.matching import ProductMappingException
from app.models.organization import Organization
from app.models.sync import NineyardSyncRun, SourceRecord
from app.models.vendor import Vendor, VendorContact, VendorImportProfile, VendorProduct
from app.models.watchlist import OosStatusHistory, OosWatchlistEntry

__all__ = [
    "AmazonInventorySnapshot",
    "AmazonOrderLine",
    "AmazonSyncRun",
    "AuditEvent",
    "AvailabilityEvent",
    "ImportFile",
    "ImportJob",
    "ImportJobRow",
    "MarketplaceListing",
    "NineyardSyncRun",
    "OosStatusHistory",
    "OosWatchlistEntry",
    "Organization",
    "Product",
    "ProductIdentifier",
    "ProductMappingException",
    "Role",
    "SourceRecord",
    "User",
    "UserRole",
    "Vendor",
    "VendorContact",
    "VendorImportProfile",
    "VendorInventorySnapshot",
    "VendorProduct",
]
