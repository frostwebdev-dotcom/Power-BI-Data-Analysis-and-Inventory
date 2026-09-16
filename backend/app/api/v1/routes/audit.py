"""Read-only audit log feed (AC-12, phase 10).

Every role may read it: the audit log is how the system explains itself.
There is no write path here — rows are written by the services that make
the changes, and the table refuses UPDATE and DELETE.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.security import Principal
from app.db.session import get_db
from app.models import AuditEvent, User
from app.models.enums import ActorType
from app.repositories.scoping import ScopedRepository

router = APIRouter(prefix="/audit", tags=["audit"])
_read = Depends(require_roles(*READ_ROLES))


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_type: ActorType
    actor_user_id: uuid.UUID | None
    actor_label: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    changed_fields: list[str] | None
    request_id: str | None
    summary: str | None
    occurred_at: datetime


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    page: int
    page_size: int
    total: int
    #: Distinct entity types and actions in this organization, for filters.
    entity_types: list[str]
    actions: list[str]


class _AuditRepository(ScopedRepository):
    pass


@router.get(
    "/events",
    response_model=AuditEventListResponse,
    summary="The audit log, newest first",
    responses={403: {"description": "The caller lacks the required role."}},
)
def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    entity_type: str | None = Query(None, max_length=100),
    entity_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None, max_length=100, description="Exact action or a prefix."),
    actor: str | None = Query(None, max_length=200, description="Actor email or label."),
    occurred_from: datetime | None = Query(None),
    occurred_to: datetime | None = Query(None),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> AuditEventListResponse:
    repository = _AuditRepository(session, principal.organization_id)
    statement = repository.select(AuditEvent)
    if entity_type:
        statement = statement.where(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        statement = statement.where(AuditEvent.entity_id == entity_id)
    if action:
        statement = statement.where(AuditEvent.action.like(f"{action}%"))
    if actor:
        pattern = f"%{actor.strip()}%"
        user_ids = select(User.id).where(
            User.organization_id == principal.organization_id, User.email.ilike(pattern)
        )
        statement = statement.where(
            AuditEvent.actor_label.ilike(pattern) | AuditEvent.actor_user_id.in_(user_ids)
        )
    if occurred_from is not None:
        statement = statement.where(AuditEvent.occurred_at >= occurred_from)
    if occurred_to is not None:
        statement = statement.where(AuditEvent.occurred_at <= occurred_to)

    total = session.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
    items = (
        session.execute(
            statement.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    entity_types = (
        session.execute(
            repository.select(AuditEvent, AuditEvent.entity_type)
            .distinct()
            .order_by(AuditEvent.entity_type)
        )
        .scalars()
        .all()
    )
    actions = (
        session.execute(
            repository.select(AuditEvent, AuditEvent.action).distinct().order_by(AuditEvent.action)
        )
        .scalars()
        .all()
    )
    return AuditEventListResponse(
        items=[AuditEventResponse.model_validate(e) for e in items],
        page=page,
        page_size=page_size,
        total=total,
        entity_types=list(entity_types),
        actions=list(actions),
    )
