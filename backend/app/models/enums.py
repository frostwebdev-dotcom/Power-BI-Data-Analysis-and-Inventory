"""Enumerations backing native PostgreSQL enum types.

Each of these becomes a real PostgreSQL enum, created and altered through
Alembic. A native enum rejects an unknown value at the database boundary, which
matters here: a mistyped match method or availability status is a correctness
bug, not a display bug.

Member name and value are kept identical so the stored representation is
readable in raw SQL.
"""

from __future__ import annotations

from enum import StrEnum


class ProductStatus(StrEnum):
    """Lifecycle of a catalog item. Products are deactivated, never deleted."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DISCONTINUED = "DISCONTINUED"
    ARCHIVED = "ARCHIVED"


class IdentifierType(StrEnum):
    """Kinds of identifier that can point at a product.

    ``CATALOG_ITEM_NUMBER`` and ``UPC`` drive match priorities 1 and 2;
    ``VENDOR_SKU`` and ``AMAZON_SKU`` carry vendor and marketplace context
    (CLAUDE.md §5.1).
    """

    CATALOG_ITEM_NUMBER = "CATALOG_ITEM_NUMBER"
    UPC = "UPC"
    EAN = "EAN"
    GTIN = "GTIN"
    ASIN = "ASIN"
    MPN = "MPN"
    AMAZON_SKU = "AMAZON_SKU"
    VENDOR_SKU = "VENDOR_SKU"


class SourceSystem(StrEnum):
    """Where a record or identifier came from."""

    NINEYARD = "NINEYARD"
    VENDOR_IMPORT = "VENDOR_IMPORT"
    MARKETPLACE = "MARKETPLACE"
    MANUAL = "MANUAL"


class Marketplace(StrEnum):
    """Sales channels a product can be listed on.

    Only Amazon in Milestone 1. Storing an Amazon SKU is in scope; calling the
    SP-API is not (CLAUDE.md §3). Other channels are added by migration when
    their milestone arrives.
    """

    AMAZON = "AMAZON"


class ListingStatus(StrEnum):
    """State of a marketplace listing as last known to us."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUPPRESSED = "SUPPRESSED"
    UNKNOWN = "UNKNOWN"


class MappingStatus(StrEnum):
    """State of a product mapping.

    ``APPROVED`` is permanent and is what match priorities 3 and 4 read. It is
    superseded only by an explicit, audited human action — never recomputed by
    an automated run (CLAUDE.md §5.2).
    """

    UNMAPPED = "UNMAPPED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class MatchMethod(StrEnum):
    """Which rule in the priority chain decided a match.

    Recorded on every matched row so any outcome can be explained after the
    fact. There is deliberately no ``DESCRIPTION`` member: a description may
    never be the sole basis for an automatic match (CLAUDE.md §5.2).
    """

    UPC = "UPC"
    CATALOG_ITEM_NUMBER = "CATALOG_ITEM_NUMBER"
    VENDOR_SKU_MAPPING = "VENDOR_SKU_MAPPING"
    AMAZON_SKU_MAPPING = "AMAZON_SKU_MAPPING"
    MANUAL_APPROVAL = "MANUAL_APPROVAL"


class MatchResult(StrEnum):
    """Outcome of evaluating the priority chain against one row."""

    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"
    SUGGESTION_ONLY = "SUGGESTION_ONLY"


class VendorStatus(StrEnum):
    """Operational state of a supplier."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ON_HOLD = "ON_HOLD"


class FileFormat(StrEnum):
    """Vendor file formats supported in Milestone 1."""

    CSV = "CSV"
    XLSX = "XLSX"


class ImportJobStatus(StrEnum):
    """Lifecycle of one vendor import."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ImportJobStage(StrEnum):
    """Where a RUNNING import is (phase 6).

    ``ImportJobStatus`` deliberately has no per-stage members; this column
    says which stage a running job is in, and is null once it is not running.
    A job whose stage is MATCHING with nothing matched is waiting for the
    matcher, not stuck: parsing sets it on completion.
    """

    PARSING = "PARSING"
    MATCHING = "MATCHING"
    SNAPSHOTTING = "SNAPSHOTTING"


class ImportRowStatus(StrEnum):
    """Validation outcome for one source row."""

    PENDING = "PENDING"
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class AvailabilityStatus(StrEnum):
    """Canonical availability, mapped from each vendor's own vocabulary.

    A vendor value the import profile does not recognise becomes ``UNKNOWN``
    plus a warning — never a silent ``AVAILABLE`` (risk R6).
    """

    AVAILABLE = "AVAILABLE"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    DISCONTINUED = "DISCONTINUED"
    UNKNOWN = "UNKNOWN"


class AvailabilityEventType(StrEnum):
    """Direction of a detected availability transition."""

    BECAME_AVAILABLE = "BECAME_AVAILABLE"
    BECAME_UNAVAILABLE = "BECAME_UNAVAILABLE"


class ExceptionReason(StrEnum):
    """Why a row could not be matched automatically."""

    NO_MATCH = "NO_MATCH"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    SUGGESTION_ONLY = "SUGGESTION_ONLY"
    CONFLICTING_IDENTIFIER = "CONFLICTING_IDENTIFIER"


class ExceptionStatus(StrEnum):
    """Review state of an exception-queue item."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OosStatus(StrEnum):
    """Stock state of a watched product."""

    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"


class WatchlistPriority(StrEnum):
    """How urgently the company wants to buy a watched product."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SyncStatus(StrEnum):
    """Lifecycle of a source-system synchronisation run (Nineyard or Amazon)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TriggerType(StrEnum):
    """What initiated a background run."""

    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"


class ActorType(StrEnum):
    """Who performed an audited action."""

    USER = "USER"
    SYSTEM = "SYSTEM"
    WORKER = "WORKER"


class AmazonSyncJobType(StrEnum):
    """Which SP-API read an Amazon ingestion run performs (ADR 0011).

    One run does one kind of read, so a failure in the orders report does not
    mask a successful inventory pull, and each kind can be scheduled on its
    own cadence.
    """

    ORDERS_REPORT = "ORDERS_REPORT"
    FBA_INVENTORY = "FBA_INVENTORY"
    LISTINGS_REPORT = "LISTINGS_REPORT"
