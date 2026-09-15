"""Vendor data access — every statement built through the tenant scope (ADR 0012).

Repositories own SQL and return rows or plain values. They never commit
(the service's transaction does), never raise HTTP errors, and never
delete: vendors and contacts are deactivated, which is a plain update.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select

from app.models.enums import VendorStatus
from app.models.vendor import Vendor, VendorContact, VendorImportProfile
from app.repositories.scoping import ScopedRepository

MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class VendorPage:
    items: Sequence[Vendor]
    page: int
    page_size: int
    total: int


class VendorRepository(ScopedRepository):
    """Reads and writes for one organization's vendors."""

    # --- vendors ---------------------------------------------------------------

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status: VendorStatus | None = None,
        q: str | None = None,
        include_inactive: bool = True,
    ) -> VendorPage:
        """Paginated, newest last by name.

        ``q`` searches name and code with ``ILIKE '%q%'``; the trigram GIN
        index on ``vendors.name`` is what makes that usable on a large table.
        """
        page = max(page, 1)
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))

        statement = self.select(Vendor)
        if status is not None:
            statement = statement.where(Vendor.status == status)
        if not include_inactive:
            statement = statement.where(Vendor.is_active.is_(True))
        if q:
            pattern = f"%{q.strip()}%"
            statement = statement.where(or_(Vendor.name.ilike(pattern), Vendor.code.ilike(pattern)))

        # The subquery already carries the tenant filter; scoping the count
        # again would add `vendors` to FROM a second time (a cartesian product).
        total = self.session.execute(
            select(func.count()).select_from(statement.subquery())
        ).scalar_one()
        items = (
            self.session.execute(
                statement.order_by(Vendor.name, Vendor.code)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return VendorPage(items=items, page=page, page_size=page_size, total=total)

    def get(self, vendor_id: uuid.UUID) -> Vendor | None:
        return self.session.execute(
            self.select(Vendor).where(Vendor.id == vendor_id)
        ).scalar_one_or_none()

    def get_by_code(self, code: str) -> Vendor | None:
        return self.session.execute(
            self.select(Vendor).where(Vendor.code == code)
        ).scalar_one_or_none()

    def add(self, vendor: Vendor) -> Vendor:
        """Attach a new vendor to the tenant and flush so its id exists."""
        vendor.organization_id = self.scope.organization_id
        self.session.add(vendor)
        self.session.flush()
        return vendor

    def has_active_import_profiles(self, vendor_id: uuid.UUID) -> bool:
        count = self.session.execute(
            self.select(VendorImportProfile, func.count()).where(
                VendorImportProfile.vendor_id == vendor_id,
                VendorImportProfile.is_active.is_(True),
            )
        ).scalar_one()
        return bool(count)

    # --- contacts ---------------------------------------------------------------

    def list_contacts(self, vendor_id: uuid.UUID) -> Sequence[VendorContact]:
        return (
            self.session.execute(
                self.select(VendorContact)
                .where(VendorContact.vendor_id == vendor_id)
                .order_by(VendorContact.is_primary.desc(), VendorContact.created_at)
            )
            .scalars()
            .all()
        )

    def get_contact(self, vendor_id: uuid.UUID, contact_id: uuid.UUID) -> VendorContact | None:
        return self.session.execute(
            self.select(VendorContact).where(
                VendorContact.vendor_id == vendor_id, VendorContact.id == contact_id
            )
        ).scalar_one_or_none()

    def get_contact_by_email(self, vendor_id: uuid.UUID, email: str) -> VendorContact | None:
        return self.session.execute(
            self.select(VendorContact).where(
                VendorContact.vendor_id == vendor_id,
                func.lower(VendorContact.email) == email.lower(),
            )
        ).scalar_one_or_none()

    def primary_contact(self, vendor_id: uuid.UUID) -> VendorContact | None:
        return self.session.execute(
            self.select(VendorContact).where(
                VendorContact.vendor_id == vendor_id,
                VendorContact.is_primary.is_(True),
                VendorContact.is_active.is_(True),
            )
        ).scalar_one_or_none()

    def add_contact(self, contact: VendorContact) -> VendorContact:
        contact.organization_id = self.scope.organization_id
        self.session.add(contact)
        self.session.flush()
        return contact
