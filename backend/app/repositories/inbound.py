"""Tenant-scoped inbound ledger heads and serialized exact-request retries."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.inbound import InboundEvidenceRevision
from app.repositories.scoping import ScopedRepository


class InboundRepository(ScopedRepository):
    def lock_request(self, request_id: uuid.UUID) -> None:
        self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        f"inbound-request:{self.scope.organization_id}:{request_id}", 0
                    )
                )
            )
        )

    def get(self, identifier: uuid.UUID) -> InboundEvidenceRevision | None:
        row = self.session.scalar(
            self.select(InboundEvidenceRevision)
            .where(InboundEvidenceRevision.id == identifier)
            .execution_options(populate_existing=True)
        )

        return row if isinstance(row, InboundEvidenceRevision) else None

    def by_request(self, request_id: uuid.UUID) -> InboundEvidenceRevision | None:
        row = self.session.scalar(
            self.select(InboundEvidenceRevision).where(
                InboundEvidenceRevision.request_id == request_id
            )
        )

        return row if isinstance(row, InboundEvidenceRevision) else None

    def head(self, policy_id: uuid.UUID, pool_id: str) -> InboundEvidenceRevision | None:
        row = self.session.scalar(
            self.select(InboundEvidenceRevision)
            .where(
                InboundEvidenceRevision.policy_id == policy_id,
                InboundEvidenceRevision.pool_id == pool_id,
            )
            .order_by(InboundEvidenceRevision.revision_number.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

        return row if isinstance(row, InboundEvidenceRevision) else None

    def heads(self, policy_id: uuid.UUID) -> list[InboundEvidenceRevision]:
        model = InboundEvidenceRevision
        return list(
            self.session.scalars(
                self.select(model)
                .where(model.policy_id == policy_id)
                .distinct(model.pool_id)
                .order_by(model.pool_id, model.revision_number.desc())
            )
        )

    def page(
        self,
        policy_id: uuid.UUID,
        page: int,
        size: int,
        pool_id: str | None = None,
        heads_only: bool = True,
    ) -> tuple[list[InboundEvidenceRevision], int]:
        model = InboundEvidenceRevision
        query = self.select(model).where(model.policy_id == policy_id)
        if pool_id is not None:
            query = query.where(model.pool_id == pool_id)
        if heads_only:
            ids = (
                self.select(model, model.id)
                .where(model.policy_id == policy_id)
                .distinct(model.pool_id)
                .order_by(model.pool_id, model.revision_number.desc())
            )
            query = query.where(model.id.in_(ids))
        total = self.session.scalar(query.with_only_columns(func.count())) or 0
        rows = self.session.scalars(
            query.order_by(model.recorded_at.desc(), model.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list(rows), total
