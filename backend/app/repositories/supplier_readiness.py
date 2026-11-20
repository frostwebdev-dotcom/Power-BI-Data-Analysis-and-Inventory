"""Scoped, bounded latest-file evidence; no older inventory fallback."""

from __future__ import annotations

import uuid

from sqlalchemy import or_

from app.models import ImportFile, ImportJob, ImportJobRow, VendorInventorySnapshot, VendorProduct
from app.repositories.scoping import ScopedRepository


class SupplierReadinessRepository(ScopedRepository):
    def jobs(self, vendor_id: uuid.UUID) -> list[ImportJob]:
        return list(
            self.session.scalars(
                self.select(ImportJob)
                .where(ImportJob.vendor_id == vendor_id)
                .order_by(ImportJob.created_at.desc(), ImportJob.id)
                .limit(2)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )

    def file(self, identifier: uuid.UUID) -> ImportFile | None:
        row = self.session.scalar(
            self.select(ImportFile)
            .where(ImportFile.id == identifier)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        return row if isinstance(row, ImportFile) else None

    def rows(self, job_id: uuid.UUID, line: VendorProduct) -> list[ImportJobRow]:
        return list(
            self.session.scalars(
                self.select(ImportJobRow)
                .where(
                    ImportJobRow.import_job_id == job_id,
                    or_(
                        ImportJobRow.vendor_product_id == line.id,
                        ImportJobRow.normalized_vendor_sku == line.normalized_vendor_sku,
                    ),
                )
                .order_by(ImportJobRow.id)
                .limit(3)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )

    def snapshots(self, job_id: uuid.UUID, line_id: uuid.UUID) -> list[VendorInventorySnapshot]:
        return list(
            self.session.scalars(
                self.select(VendorInventorySnapshot)
                .where(
                    VendorInventorySnapshot.import_job_id == job_id,
                    VendorInventorySnapshot.vendor_product_id == line_id,
                )
                .limit(2)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )
