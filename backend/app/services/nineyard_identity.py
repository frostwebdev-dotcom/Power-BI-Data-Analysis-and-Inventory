"""Serialize changes to a tenant's Nineyard catalogue, including CSV imports."""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_catalogue(session: Session, organization_id: uuid.UUID) -> None:
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:catalogue, 0))"),
        {"catalogue": f"nineyard-catalogue:{organization_id}"},
    )
