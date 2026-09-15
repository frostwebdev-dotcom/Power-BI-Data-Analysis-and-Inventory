"""Import profiles: versioned, validated, audited (AC-6, ADR 0005, ADR 0013).

Versioning is the rule that matters. A profile line (vendor + name) has many
versions and at most one active; **editing never mutates a version**.
``update_profile`` inserts version *n+1* with the requested changes applied
over version *n*, and deactivates *n* — one transaction, two audit rows.
An import job that ran under version *n* keeps pointing at *n*, so it stays
interpretable forever (AC-6.3).

The validate-against-sample path reads a file, applies the rules, and
returns a preview. It stores nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import transaction
from app.imports.mapping import MappingPreview, map_table
from app.imports.profile_rules import ProfileRules
from app.imports.readers import ReadOptions, UnreadableFile, read_table
from app.models.vendor import VendorImportProfile
from app.repositories.import_profiles import ImportProfileRepository
from app.schemas.import_profiles import ImportProfileCreate, ImportProfileUpdate
from app.services import audit
from app.services.vendors import get_vendor

_logger = get_logger(__name__)

PREVIEW_ROWS = 20
#: Uploads larger than this are refused: the preview reads the first rows
#: only, but the whole body still has to arrive.
MAX_SAMPLE_BYTES = 25 * 1024 * 1024


class ImportProfileNotFound(NotFoundError):  # noqa: N818
    code = "import_profile_not_found"
    message = "No such import profile for this vendor."


class ImportProfileNameTaken(ConflictError):  # noqa: N818
    code = "import_profile_name_taken"
    message = "An active profile with this name already exists for this vendor."


class ImportProfileNotActive(ConflictError):  # noqa: N818
    code = "import_profile_not_active"
    message = (
        "Only the active version of a profile can be edited or deactivated. "
        "Older versions are kept for the imports that ran under them."
    )


class ImportProfileInvalid(ValidationFailedError):  # noqa: N818
    code = "import_profile_invalid"
    message = "The stored profile no longer satisfies the rule shapes."


class SampleFileUnreadable(ValidationFailedError):  # noqa: N818
    code = "sample_file_unreadable"
    message = "The sample file could not be read as the profile describes."


class SampleFileTooLarge(ValidationFailedError):  # noqa: N818
    code = "sample_file_too_large"
    message = f"Sample files are limited to {MAX_SAMPLE_BYTES // (1024 * 1024)} MB."


# --- reads ---------------------------------------------------------------------------


def list_profiles(
    session: Session, principal: Principal, vendor_id: uuid.UUID, *, include_inactive: bool
) -> list[VendorImportProfile]:
    get_vendor(session, principal, vendor_id)
    return list(
        ImportProfileRepository(session, principal.organization_id).list_for_vendor(
            vendor_id, include_inactive=include_inactive
        )
    )


def get_profile(
    session: Session, principal: Principal, vendor_id: uuid.UUID, profile_id: uuid.UUID
) -> VendorImportProfile:
    get_vendor(session, principal, vendor_id)
    profile = ImportProfileRepository(session, principal.organization_id).get(vendor_id, profile_id)
    if profile is None:
        raise ImportProfileNotFound()
    return profile


def rules_of(profile: VendorImportProfile) -> ProfileRules:
    """The stored JSONB, re-validated. A row that fails is reported, not used."""
    try:
        return ProfileRules.from_columns(
            column_map=profile.column_map,
            normalization_rules=profile.normalization_rules,
            availability_rules=profile.availability_rules,
            quantity_semantics=profile.quantity_semantics,
            price_semantics=profile.price_semantics,
            pack_size_handling=profile.pack_size_handling,
        )
    except ValueError as exc:
        raise ImportProfileInvalid(details={"reason": str(exc)}) from exc


# --- mutations -------------------------------------------------------------------------


def create_profile(
    session: Session, principal: Principal, vendor_id: uuid.UUID, payload: ImportProfileCreate
) -> VendorImportProfile:
    vendor = get_vendor(session, principal, vendor_id)
    repository = ImportProfileRepository(session, principal.organization_id)
    if repository.active_by_name(vendor.id, payload.name) is not None:
        raise ImportProfileNameTaken()

    profile = VendorImportProfile(
        vendor_id=vendor.id,
        name=payload.name,
        version=repository.latest_version(vendor.id, payload.name) + 1,
        is_active=True,
        created_by_user_id=principal.user_id,
        header_signature=payload.header_signature,
        **_file_columns(payload),
        **payload.rules().as_columns(),
    )
    try:
        with transaction(session):
            repository.add(profile)
            audit.record_change(
                session,
                organization_id=principal.organization_id,
                action="import_profile.created",
                instance=profile,
                before=None,
                actor=principal,
                summary=(
                    f"Import profile {profile.name} v{profile.version} created for {vendor.code}."
                ),
            )
    except IntegrityError as exc:
        raise ImportProfileNameTaken() from exc
    return profile


def update_profile(
    session: Session,
    principal: Principal,
    vendor_id: uuid.UUID,
    profile_id: uuid.UUID,
    payload: ImportProfileUpdate,
) -> VendorImportProfile:
    """Create version n+1 from the active version n, then retire n.

    Refused on an inactive version: history is not edited. An update with
    nothing set is a no-op — no new version for no change.
    """
    current = get_profile(session, principal, vendor_id, profile_id)
    if not current.is_active:
        raise ImportProfileNotActive()
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return current

    merged = _merge(current, payload)
    successor = VendorImportProfile(
        vendor_id=current.vendor_id,
        name=current.name,
        version=current.version + 1,
        is_active=True,
        created_by_user_id=principal.user_id,
        header_signature=merged.header_signature,
        **_file_columns(merged),
        **merged.rules().as_columns(),
    )
    repository = ImportProfileRepository(session, principal.organization_id)
    before_current = audit.snapshot(current)
    with transaction(session):
        # Retire n first: the partial unique index allows one active per name.
        current.is_active = False
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="import_profile.superseded",
            instance=current,
            before=before_current,
            actor=principal,
            summary=(
                f"Import profile {current.name} v{current.version} "
                f"superseded by v{successor.version}."
            ),
        )
        repository.add(successor)
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="import_profile.version_created",
            instance=successor,
            before=None,
            actor=principal,
            summary=(
                f"Import profile {successor.name} v{successor.version} created: "
                f"{', '.join(sorted(changes))}."
            ),
        )
    _logger.info(
        "import_profile.versioned",
        profile_id=str(successor.id),
        name=successor.name,
        version=successor.version,
    )
    return successor


def deactivate_profile(
    session: Session, principal: Principal, vendor_id: uuid.UUID, profile_id: uuid.UUID
) -> VendorImportProfile:
    profile = get_profile(session, principal, vendor_id, profile_id)
    if not profile.is_active:
        raise ImportProfileNotActive()
    before = audit.snapshot(profile)
    with transaction(session):
        profile.is_active = False
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="import_profile.deactivated",
            instance=profile,
            before=before,
            actor=principal,
            summary=f"Import profile {profile.name} v{profile.version} deactivated.",
        )
    return profile


# --- validate against a sample file ----------------------------------------------------


def preview_file(
    rules: ProfileRules,
    options: ReadOptions,
    content: bytes,
    *,
    expected_signature: str | None,
) -> tuple[MappingPreview, Any]:
    """Read the first rows of ``content`` as ``options`` say and map them."""
    if len(content) > MAX_SAMPLE_BYTES:
        raise SampleFileTooLarge()
    try:
        table = read_table(content, options, max_rows=PREVIEW_ROWS)
    except UnreadableFile as exc:
        raise SampleFileUnreadable(details={"reason": str(exc)}) from exc
    preview = map_table(rules, table.headers, table.rows, expected_signature=expected_signature)
    return preview, table


def read_options_of(profile: VendorImportProfile | ImportProfileCreate) -> ReadOptions:
    return ReadOptions(
        file_format=profile.file_format,
        encoding=profile.encoding,
        delimiter=profile.delimiter,
        quote_char=profile.quote_char,
        header_row_index=profile.header_row_index,
        skip_rows=profile.skip_rows,
        sheet_name=profile.sheet_name,
        sheet_index=profile.sheet_index,
    )


# --- helpers ---------------------------------------------------------------------------


def _file_columns(payload: ImportProfileCreate) -> dict[str, Any]:
    return {
        "file_format": payload.file_format,
        "encoding": payload.encoding,
        "delimiter": payload.delimiter,
        "quote_char": payload.quote_char,
        "header_row_index": payload.header_row_index,
        "skip_rows": payload.skip_rows,
        "sheet_name": payload.sheet_name,
        "sheet_index": payload.sheet_index,
    }


def _merge(current: VendorImportProfile, payload: ImportProfileUpdate) -> ImportProfileCreate:
    """Version n's values with the update's set fields applied, re-validated whole."""
    base: dict[str, Any] = {
        "name": current.name,
        "file_format": current.file_format,
        "encoding": current.encoding,
        "delimiter": current.delimiter,
        "quote_char": current.quote_char,
        "header_row_index": current.header_row_index,
        "skip_rows": current.skip_rows,
        "sheet_name": current.sheet_name,
        "sheet_index": current.sheet_index,
        "column_map": current.column_map,
        "normalization_rules": current.normalization_rules,
        "availability_rules": current.availability_rules,
        "quantity_semantics": current.quantity_semantics,
        "price_semantics": current.price_semantics,
        "pack_size_handling": current.pack_size_handling,
        "header_signature": current.header_signature,
    }
    base.update(payload.model_dump(exclude_unset=True, mode="json"))
    try:
        return ImportProfileCreate.model_validate(base)
    except ValueError as exc:
        raise ValidationFailedError(details={"reason": str(exc)}) from exc
