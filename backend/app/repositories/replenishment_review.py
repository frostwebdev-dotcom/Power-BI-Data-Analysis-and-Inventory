"""Scoped latest-record projections and bounded catalogue-label search."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import Select, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH

from app.models import (
    Product,
    PurchasingPolicyRevision,
    ReplenishmentRecommendation,
    Vendor,
    VendorProduct,
)
from app.repositories.scoping import ScopedRepository


class ReplenishmentReviewRepository(ScopedRepository):
    def query(self) -> Select[Any]:
        records = self.select(
            ReplenishmentRecommendation,
            ReplenishmentRecommendation.id,
            func.row_number()
            .over(
                partition_by=ReplenishmentRecommendation.policy_id,
                order_by=(
                    ReplenishmentRecommendation.recorded_at.desc(),
                    ReplenishmentRecommendation.id.desc(),
                ),
            )
            .label("position"),
        ).subquery()
        decisions = (
            self.select(
                PurchasingPolicyRevision,
                PurchasingPolicyRevision.policy_id,
                PurchasingPolicyRevision.id,
                PurchasingPolicyRevision.kind,
                func.row_number()
                .over(
                    partition_by=PurchasingPolicyRevision.policy_id,
                    order_by=PurchasingPolicyRevision.revision_number.desc(),
                )
                .label("position"),
            )
            .where(PurchasingPolicyRevision.kind != "DRAFT")
            .subquery()
        )
        current = func.coalesce(
            and_(
                decisions.c.kind == "APPROVED",
                decisions.c.id == ReplenishmentRecommendation.approved_revision_id,
            ),
            False,
        )
        positive = func.jsonb_path_exists(
            ReplenishmentRecommendation.snapshot,
            cast("$.pools[*].result.calculation.net_shortfall_units ? (@ > 0)", JSONPATH),
        )
        return (
            self.select(
                ReplenishmentRecommendation,
                ReplenishmentRecommendation,
                Product.catalog_item_number,
                Product.name.label("product_name"),
                Vendor.id.label("vendor_id"),
                Vendor.code.label("vendor_code"),
                Vendor.name.label("vendor_name"),
                VendorProduct.vendor_sku,
                current.label("approval_current"),
                positive.label("positive_shortfall"),
            )
            .join(
                records,
                and_(records.c.id == ReplenishmentRecommendation.id, records.c.position == 1),
            )
            .join(
                Product,
                and_(
                    Product.id == ReplenishmentRecommendation.product_id,
                    Product.organization_id == self.scope.organization_id,
                ),
            )
            .join(
                VendorProduct,
                and_(
                    VendorProduct.id == ReplenishmentRecommendation.vendor_product_id,
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
            .outerjoin(
                decisions,
                and_(
                    decisions.c.policy_id == ReplenishmentRecommendation.policy_id,
                    decisions.c.position == 1,
                ),
            )
        )

    def page(
        self,
        page: int,
        size: int,
        *,
        q: str | None = None,
        product_id: uuid.UUID | None = None,
        vendor_id: uuid.UUID | None = None,
        policy_id: uuid.UUID | None = None,
        status: str | None = None,
        approval: Literal["ALL", "CURRENT", "CHANGED"] = "CURRENT",
        shortfall_only: bool = False,
    ) -> tuple[list[Any], int]:
        query = self.query()
        for column, value in (
            (ReplenishmentRecommendation.product_id, product_id),
            (Vendor.id, vendor_id),
            (ReplenishmentRecommendation.policy_id, policy_id),
            (ReplenishmentRecommendation.status, status),
        ):
            if value is not None:
                query = query.where(column == value)
        columns = query.selected_columns
        if approval != "ALL":
            query = query.where(columns.approval_current.is_(approval == "CURRENT"))
        if shortfall_only:
            query = query.where(columns.positive_shortfall.is_(True))
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(
                or_(
                    *(
                        column.ilike(pattern, escape="\\")
                        for column in (
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
        rows = list(
            self.session.execute(
                query.order_by(
                    ReplenishmentRecommendation.recorded_at.desc(),
                    ReplenishmentRecommendation.id.desc(),
                )
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        return rows, total

    def counts(self) -> dict[str, int]:
        query = self.query()
        sub = query.with_only_columns(
            ReplenishmentRecommendation.id,
            ReplenishmentRecommendation.status,
            query.selected_columns.approval_current,
            query.selected_columns.positive_shortfall,
        ).subquery()
        current = sub.c.approval_current.is_(True)
        fields = {
            "latest_saved_total": func.count(),
            "current_approval_total": func.count().filter(current),
            "approval_changed_total": func.count().filter(sub.c.approval_current.is_(False)),
            "current_approval_blocked": func.count().filter(current, sub.c.status == "BLOCKED"),
            "current_approval_review_required": func.count().filter(
                current, sub.c.status == "REVIEW_REQUIRED"
            ),
            "current_approval_no_reorder": func.count().filter(
                current, sub.c.status == "NO_REORDER"
            ),
            "current_approval_positive_shortfall": func.count().filter(
                current, sub.c.positive_shortfall.is_(True)
            ),
        }
        result = self.session.execute(
            select(*(value.label(key) for key, value in fields.items())).select_from(sub)
        ).one()
        return {key: int(result._mapping[key]) for key in fields}
