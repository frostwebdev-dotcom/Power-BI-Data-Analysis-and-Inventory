"""Scoped historical reads and serialized request retries."""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import func, select

from app.models import ReplenishmentRecommendation
from app.repositories.scoping import ScopedRepository


class RecommendationRepository(ScopedRepository):
    def lock_request(self, identifier: uuid.UUID) -> None:
        self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        f"replenishment-request:{self.scope.organization_id}:{identifier}", 0
                    )
                )
            )
        )

    def get(self, identifier: uuid.UUID) -> ReplenishmentRecommendation | None:
        row = self.session.scalar(
            self.select(ReplenishmentRecommendation).where(
                ReplenishmentRecommendation.id == identifier
            )
        )
        return row if isinstance(row, ReplenishmentRecommendation) else None

    def by_request(self, identifier: uuid.UUID) -> ReplenishmentRecommendation | None:
        row = self.session.scalar(
            self.select(ReplenishmentRecommendation).where(
                ReplenishmentRecommendation.request_id == identifier
            )
        )
        return row if isinstance(row, ReplenishmentRecommendation) else None

    def page(
        self,
        page: int,
        size: int,
        *,
        policy_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        status: Literal["BLOCKED", "NO_REORDER"] | None = None,
    ) -> tuple[list[ReplenishmentRecommendation], int]:
        query = self.select(ReplenishmentRecommendation)
        for column, value in (
            (ReplenishmentRecommendation.policy_id, policy_id),
            (ReplenishmentRecommendation.product_id, product_id),
            (ReplenishmentRecommendation.status, status),
        ):
            if value is not None:
                query = query.where(column == value)
        total = self.session.scalar(query.with_only_columns(func.count())) or 0
        rows = self.session.scalars(
            query.order_by(
                ReplenishmentRecommendation.recorded_at.desc(), ReplenishmentRecommendation.id
            )
            .offset((page - 1) * size)
            .limit(size)
        )
        return list(rows), total
