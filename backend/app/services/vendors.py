"""The vendor service — every mutation audited in the same transaction (AC-4).

The shape every later service copies:

* the route hands in the ``Principal`` and a validated schema;
* the service opens **one** ``transaction()``, makes the change through the
  tenant-scoped repository, and writes the audit row with
  ``audit.record_change`` before the block ends — so the change and its
  trail commit together or not at all (ADR 0006);
* business refusals are ``AppError`` subclasses, so the client gets the
  standard envelope with a typed ``code`` rather than a 500.

Nothing here deletes. A vendor with any history is deactivated (AC-4.3).
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import transaction
from app.models.enums import VendorStatus
from app.models.vendor import Vendor, VendorContact
from app.repositories.vendors import VendorPage, VendorRepository
from app.schemas.vendors import (
    VendorContactCreate,
    VendorContactUpdate,
    VendorCreate,
    VendorUpdate,
)
from app.services import audit

_logger = get_logger(__name__)


class VendorNotFound(NotFoundError):  # noqa: N818 — an HTTP outcome, not a fault
    code = "vendor_not_found"
    message = "No such vendor."


class VendorContactNotFound(NotFoundError):  # noqa: N818
    code = "vendor_contact_not_found"
    message = "No such vendor contact."


class VendorCodeTaken(ConflictError):  # noqa: N818
    code = "vendor_code_taken"
    message = "A vendor with this code already exists."


class VendorHasActiveProfiles(ConflictError):  # noqa: N818
    code = "vendor_has_active_import_profiles"
    message = (
        "This vendor has active import profiles. Deactivate them first; a vendor "
        "with live ingestion configuration cannot be retired underneath it."
    )


class VendorAlreadyInactive(ConflictError):  # noqa: N818
    code = "vendor_already_inactive"
    message = "This vendor is already inactive."


class ContactEmailTaken(ConflictError):  # noqa: N818
    code = "vendor_contact_email_taken"
    message = "This vendor already has a contact with that email address."


class ContactAlreadyInactive(ConflictError):  # noqa: N818
    code = "vendor_contact_already_inactive"
    message = "This contact is already inactive."


# --- reads ----------------------------------------------------------------------------


def list_vendors(
    session: Session,
    principal: Principal,
    *,
    page: int,
    page_size: int,
    status: VendorStatus | None,
    q: str | None,
) -> VendorPage:
    return VendorRepository(session, principal.organization_id).list(
        page=page, page_size=page_size, status=status, q=q
    )


def get_vendor(session: Session, principal: Principal, vendor_id: uuid.UUID) -> Vendor:
    vendor = VendorRepository(session, principal.organization_id).get(vendor_id)
    if vendor is None:
        raise VendorNotFound()
    return vendor


# --- vendor mutations ------------------------------------------------------------------


def create_vendor(session: Session, principal: Principal, payload: VendorCreate) -> Vendor:
    repository = VendorRepository(session, principal.organization_id)
    if repository.get_by_code(payload.code) is not None:
        raise VendorCodeTaken()

    vendor = Vendor(**payload.model_dump())
    try:
        with transaction(session):
            repository.add(vendor)
            audit.record_change(
                session,
                organization_id=principal.organization_id,
                action="vendor.created",
                instance=vendor,
                before=None,
                actor=principal,
                summary=f"Vendor {vendor.code} created.",
            )
    except IntegrityError as exc:
        # Lost the race with a concurrent create of the same code: the unique
        # index is the truth, the pre-check above is only the friendly path.
        raise VendorCodeTaken() from exc
    _logger.info("vendor.created", vendor_id=str(vendor.id), code=vendor.code)
    return vendor


def update_vendor(
    session: Session, principal: Principal, vendor_id: uuid.UUID, payload: VendorUpdate
) -> Vendor:
    vendor = get_vendor(session, principal, vendor_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return vendor

    before = audit.snapshot(vendor)
    with transaction(session):
        for field, value in changes.items():
            setattr(vendor, field, value)
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="vendor.updated",
            instance=vendor,
            before=before,
            actor=principal,
            summary=f"Vendor {vendor.code} updated: {', '.join(sorted(changes))}.",
        )
    return vendor


def deactivate_vendor(session: Session, principal: Principal, vendor_id: uuid.UUID) -> Vendor:
    """Retire a vendor. Refused while it still has active import profiles."""
    repository = VendorRepository(session, principal.organization_id)
    vendor = get_vendor(session, principal, vendor_id)
    if not vendor.is_active:
        raise VendorAlreadyInactive()
    if repository.has_active_import_profiles(vendor.id):
        raise VendorHasActiveProfiles()

    before = audit.snapshot(vendor)
    with transaction(session):
        vendor.is_active = False
        vendor.status = VendorStatus.INACTIVE
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="vendor.deactivated",
            instance=vendor,
            before=before,
            actor=principal,
            summary=f"Vendor {vendor.code} deactivated.",
        )
    _logger.info("vendor.deactivated", vendor_id=str(vendor.id), code=vendor.code)
    return vendor


# --- contacts -------------------------------------------------------------------------


def list_contacts(
    session: Session, principal: Principal, vendor_id: uuid.UUID
) -> list[VendorContact]:
    get_vendor(session, principal, vendor_id)
    return list(VendorRepository(session, principal.organization_id).list_contacts(vendor_id))


def get_contact(
    session: Session, principal: Principal, vendor_id: uuid.UUID, contact_id: uuid.UUID
) -> VendorContact:
    get_vendor(session, principal, vendor_id)
    contact = VendorRepository(session, principal.organization_id).get_contact(
        vendor_id, contact_id
    )
    if contact is None:
        raise VendorContactNotFound()
    return contact


def add_contact(
    session: Session, principal: Principal, vendor_id: uuid.UUID, payload: VendorContactCreate
) -> VendorContact:
    repository = VendorRepository(session, principal.organization_id)
    vendor = get_vendor(session, principal, vendor_id)
    if repository.get_contact_by_email(vendor.id, payload.email) is not None:
        raise ContactEmailTaken()

    contact = VendorContact(vendor_id=vendor.id, **payload.model_dump())
    try:
        with transaction(session):
            if payload.is_primary:
                _demote_primary(session, principal, repository, vendor.id)
            repository.add_contact(contact)
            audit.record_change(
                session,
                organization_id=principal.organization_id,
                action="vendor_contact.created",
                instance=contact,
                before=None,
                actor=principal,
                summary=f"Contact {contact.email} added to vendor {vendor.code}.",
            )
    except IntegrityError as exc:
        raise ContactEmailTaken() from exc
    return contact


def update_contact(
    session: Session,
    principal: Principal,
    vendor_id: uuid.UUID,
    contact_id: uuid.UUID,
    payload: VendorContactUpdate,
) -> VendorContact:
    repository = VendorRepository(session, principal.organization_id)
    contact = get_contact(session, principal, vendor_id, contact_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return contact

    new_email = changes.get("email")
    if new_email and new_email != contact.email:
        clash = repository.get_contact_by_email(vendor_id, new_email)
        if clash is not None and clash.id != contact.id:
            raise ContactEmailTaken()

    before = audit.snapshot(contact)
    try:
        with transaction(session):
            if changes.get("is_primary") and not contact.is_primary:
                _demote_primary(session, principal, repository, vendor_id, except_id=contact.id)
            for field, value in changes.items():
                setattr(contact, field, value)
            session.flush()
            audit.record_change(
                session,
                organization_id=principal.organization_id,
                action="vendor_contact.updated",
                instance=contact,
                before=before,
                actor=principal,
                summary=f"Contact {contact.email} updated: {', '.join(sorted(changes))}.",
            )
    except IntegrityError as exc:
        raise ContactEmailTaken() from exc
    return contact


def deactivate_contact(
    session: Session, principal: Principal, vendor_id: uuid.UUID, contact_id: uuid.UUID
) -> VendorContact:
    contact = get_contact(session, principal, vendor_id, contact_id)
    if not contact.is_active:
        raise ContactAlreadyInactive()

    before = audit.snapshot(contact)
    with transaction(session):
        contact.is_active = False
        contact.is_primary = False
        session.flush()
        audit.record_change(
            session,
            organization_id=principal.organization_id,
            action="vendor_contact.deactivated",
            instance=contact,
            before=before,
            actor=principal,
            summary=f"Contact {contact.email} deactivated.",
        )
    return contact


def _demote_primary(
    session: Session,
    principal: Principal,
    repository: VendorRepository,
    vendor_id: uuid.UUID,
    *,
    except_id: uuid.UUID | None = None,
) -> None:
    """Only one active primary contact per vendor: the old one steps down, audited."""
    current = repository.primary_contact(vendor_id)
    if current is None or current.id == except_id:
        return
    before = audit.snapshot(current)
    current.is_primary = False
    session.flush()
    audit.record_change(
        session,
        organization_id=principal.organization_id,
        action="vendor_contact.updated",
        instance=current,
        before=before,
        actor=principal,
        summary=f"Contact {current.email} is no longer primary.",
    )
