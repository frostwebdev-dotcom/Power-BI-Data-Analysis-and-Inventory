"""Import-profile data access, through the tenant scope (ADR 0012)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func

from app.models.vendor import VendorImportProfile
from app.repositories.scoping import ScopedRepository


class ImportProfileRepository(ScopedRepository):
    def list_for_vendor(
        self, vendor_id: uuid.UUID, *, include_inactive: bool = False
    ) -> Sequence[VendorImportProfile]:
        statement = self.select(VendorImportProfile).where(
            VendorImportProfile.vendor_id == vendor_id
        )
        if not include_inactive:
            statement = statement.where(VendorImportProfile.is_active.is_(True))
        return (
            self.session.execute(
                statement.order_by(VendorImportProfile.name, VendorImportProfile.version.desc())
            )
            .scalars()
            .all()
        )

    def get(self, vendor_id: uuid.UUID, profile_id: uuid.UUID) -> VendorImportProfile | None:
        return self.session.execute(
            self.select(VendorImportProfile).where(
                VendorImportProfile.vendor_id == vendor_id,
                VendorImportProfile.id == profile_id,
            )
        ).scalar_one_or_none()

    def active_by_name(self, vendor_id: uuid.UUID, name: str) -> VendorImportProfile | None:
        return self.session.execute(
            self.select(VendorImportProfile).where(
                VendorImportProfile.vendor_id == vendor_id,
                VendorImportProfile.name == name,
                VendorImportProfile.is_active.is_(True),
            )
        ).scalar_one_or_none()

    def latest_version(self, vendor_id: uuid.UUID, name: str) -> int:
        value = self.session.execute(
            self.select(VendorImportProfile, func.max(VendorImportProfile.version)).where(
                VendorImportProfile.vendor_id == vendor_id,
                VendorImportProfile.name == name,
            )
        ).scalar_one()
        return int(value or 0)

    def versions(self, vendor_id: uuid.UUID, name: str) -> Sequence[VendorImportProfile]:
        return (
            self.session.execute(
                self.select(VendorImportProfile)
                .where(
                    VendorImportProfile.vendor_id == vendor_id,
                    VendorImportProfile.name == name,
                )
                .order_by(VendorImportProfile.version)
            )
            .scalars()
            .all()
        )

    def add(self, profile: VendorImportProfile) -> VendorImportProfile:
        profile.organization_id = self.scope.organization_id
        self.session.add(profile)
        self.session.flush()
        return profile
