"""Bounded scoped current-approved choices, without evaluation or source scanning."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, func, or_

from app.models import Product, PurchasingPolicyRevision, Vendor, VendorProduct
from app.repositories.scoping import ScopedRepository


class CurrentReplenishmentRepository(ScopedRepository):
    def page(
        self,
        page: int,
        size: int,
        *,
        q: str | None = None,
        product_id: uuid.UUID | None = None,
        vendor_id: uuid.UUID | None = None,
    ) -> tuple[list[Any], int]:
        model = PurchasingPolicyRevision
        decisions = (
            self.select(
                model,
                model.policy_id,
                model.id.label("approval_id"),
                model.revision_number,
                model.kind,
                func.row_number()
                .over(partition_by=model.policy_id, order_by=model.revision_number.desc())
                .label("position"),
            )
            .where(model.kind != "DRAFT")
            .subquery()
        )
        query = (
            self.select(
                model,
                model,
                decisions.c.approval_id,
                decisions.c.revision_number.label("approved_revision_number"),
                Product.catalog_item_number,
                Product.name.label("product_name"),
                Vendor.id.label("vendor_id"),
                Vendor.code.label("vendor_code"),
                Vendor.name.label("vendor_name"),
                VendorProduct.vendor_sku,
            )
            .join(decisions, and_(decisions.c.policy_id == model.id, decisions.c.position == 1))
            .join(
                Product,
                and_(
                    Product.id == model.product_id,
                    Product.organization_id == self.scope.organization_id,
                ),
            )
            .join(
                VendorProduct,
                and_(
                    VendorProduct.id == model.vendor_product_id,
                    VendorProduct.organization_id == self.scope.organization_id,
                ),
            )
            .join(
                Vendor,
                and_(
                    Vendor.id == VendorProduct.vendor_id,
                    Vendor.organization_id == self.scope.organization_id,
                ),
            )
            .where(model.revision_number == 1, decisions.c.kind == "APPROVED")
        )
        if product_id is not None:
            query = query.where(model.product_id == product_id)
        if vendor_id is not None:
            query = query.where(Vendor.id == vendor_id)
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(
                or_(
                    *(
                        c.ilike(pattern, escape="\\")
                        for c in (
                            Product.name,
                            Product.catalog_item_number,
                            Vendor.name,
                            Vendor.code,
                            VendorProduct.vendor_sku,
                        )
                    )
                )
            )
        total = self.session.scalar(query.with_only_columns(func.count()).order_by(None)) or 0
        rows = self.session.execute(
            query.order_by(
                Product.catalog_item_number, Vendor.code, VendorProduct.vendor_sku, model.id
            )
            .offset((page - 1) * size)
            .limit(size)
        )
        return list(rows), total
