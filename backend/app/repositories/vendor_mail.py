from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, or_

from app.models.vendor_mail import VendorMailDelivery, VendorMailSource, VendorMailState
from app.repositories.scoping import ScopedRepository


class VendorMailRepository(ScopedRepository):
    def sources(self, *, active_only: bool = False) -> list[VendorMailSource]:
        query = self.select(VendorMailSource)
        if active_only:
            query = query.where(VendorMailSource.is_active.is_(True))
        return list(self.session.scalars(query.order_by(VendorMailSource.name)))

    def source(self, identifier: uuid.UUID) -> VendorMailSource | None:
        row = self.session.scalar(
            self.select(VendorMailSource).where(VendorMailSource.id == identifier)
        )
        return row if isinstance(row, VendorMailSource) else None

    def state(self, account_key: str) -> VendorMailState | None:
        row = self.session.scalar(
            self.select(VendorMailState).where(VendorMailState.account_key == account_key)
        )
        return row if isinstance(row, VendorMailState) else None

    def delivery(self, identifier: uuid.UUID) -> VendorMailDelivery | None:
        row = self.session.scalar(
            self.select(VendorMailDelivery).where(VendorMailDelivery.id == identifier)
        )
        return row if isinstance(row, VendorMailDelivery) else None

    def by_message(
        self, state: uuid.UUID, source: uuid.UUID, message: str
    ) -> list[VendorMailDelivery]:
        return list(
            self.session.scalars(
                self.select(VendorMailDelivery).where(
                    VendorMailDelivery.state_id == state,
                    VendorMailDelivery.source_id == source,
                    VendorMailDelivery.message_id == message,
                )
            )
        )

    def due(self, state_id: uuid.UUID, at: datetime, limit: int) -> list[VendorMailDelivery]:
        return list(
            self.session.scalars(
                self.select(VendorMailDelivery)
                .join(VendorMailSource, VendorMailSource.id == VendorMailDelivery.source_id)
                .where(
                    VendorMailSource.organization_id == self.scope.organization_id,
                    VendorMailSource.is_active.is_(True),
                    VendorMailDelivery.state_id == state_id,
                    VendorMailDelivery.status.in_(("PENDING", "RUNNING", "FAILED")),
                    or_(
                        VendorMailDelivery.next_attempt_at.is_(None),
                        VendorMailDelivery.next_attempt_at <= at,
                    ),
                )
                .order_by(VendorMailDelivery.received_at, VendorMailDelivery.created_at)
                .limit(limit)
            )
        )

    def history(
        self, source_id: uuid.UUID | None, page: int, page_size: int
    ) -> tuple[list[VendorMailDelivery], int]:
        query = self.select(VendorMailDelivery)
        if source_id is not None:
            query = query.where(VendorMailDelivery.source_id == source_id)
        total = self.session.scalar(query.with_only_columns(func.count()).order_by(None)) or 0
        rows = list(
            self.session.scalars(
                query.order_by(VendorMailDelivery.received_at.desc(), VendorMailDelivery.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total
