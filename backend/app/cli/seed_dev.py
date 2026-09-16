"""Seed a development organization and users so the admin interface can be
signed into (phase 10).

    python -m app.cli.seed_dev --org-slug demo --org-name "Demo Distribution" \\
        --admin admin@example.test

Idempotent: re-running finds the existing rows. Refused in production and
whenever ``DEV_AUTH_ENABLED`` is false, because the users it creates have no
password — they exist to be issued development tokens.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import RoleCode
from app.db.transaction import session_scope
from app.models import Organization, User
from app.models.catalog import Product, ProductIdentifier
from app.models.enums import IdentifierType, SourceSystem
from app.services.roles import assign_role, ensure_system_roles

USERS: tuple[tuple[str, RoleCode, str], ...] = (
    ("admin", RoleCode.ADMIN, "Administrator"),
    ("buyer", RoleCode.PURCHASING_MANAGER, "Purchasing Manager"),
    ("operator", RoleCode.DATA_OPERATOR, "Data Operator"),
    ("viewer", RoleCode.VIEWER, "Viewer"),
)


def seed(session: Session, *, org_slug: str, org_name: str, domain: str) -> list[str]:
    organization = session.execute(
        select(Organization).where(Organization.slug == org_slug)
    ).scalar_one_or_none()
    if organization is None:
        organization = Organization(name=org_name, slug=org_slug)
        session.add(organization)
        session.flush()
    roles = ensure_system_roles(session, organization.id)
    emails: list[str] = []
    for local_part, role, display_name in USERS:
        email = f"{local_part}@{domain}"
        user = session.execute(
            select(User).where(User.organization_id == organization.id, User.email == email)
        ).scalar_one_or_none()
        if user is None:
            user = User(organization_id=organization.id, email=email, display_name=display_name)
            session.add(user)
            session.flush()
        assign_role(session, user=user, role=roles[role])
        emails.append(email)
    _seed_demo_product(session, organization)
    return emails


DEMO_CATALOG_ITEM_NUMBER = "DEMO-001"
DEMO_UPC = "00012345678905"  # 012345678905 in UPC-A form


def _seed_demo_product(session: Session, organization: Organization) -> None:
    """One product with one UPC, so the walk-through has something to match
    against before the Nineyard catalogue sync exists (B1)."""
    product = session.execute(
        select(Product).where(
            Product.organization_id == organization.id,
            Product.catalog_item_number == DEMO_CATALOG_ITEM_NUMBER,
        )
    ).scalar_one_or_none()
    if product is None:
        product = Product(
            organization_id=organization.id,
            catalog_item_number=DEMO_CATALOG_ITEM_NUMBER,
            name="Blue Widget 12 pack",
            brand="Demo",
        )
        session.add(product)
        session.flush()
    identifier = session.execute(
        select(ProductIdentifier).where(
            ProductIdentifier.organization_id == organization.id,
            ProductIdentifier.identifier_type == IdentifierType.UPC,
            ProductIdentifier.normalized_value == DEMO_UPC,
        )
    ).scalar_one_or_none()
    if identifier is None:
        session.add(
            ProductIdentifier(
                organization_id=organization.id,
                product_id=product.id,
                identifier_type=IdentifierType.UPC,
                raw_value="012345678905",
                normalized_value=DEMO_UPC,
                source_system=SourceSystem.MANUAL,
                has_valid_checksum=True,
                is_primary=True,
            )
        )
        session.flush()


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-slug", default="demo")
    parser.add_argument("--org-name", default="Demo Distribution")
    parser.add_argument("--domain", default="example.test", help="Email domain for the users.")
    arguments = parser.parse_args(argv)

    settings = settings or get_settings()
    if settings.is_production or not settings.dev_auth_enabled:
        print("seed_dev is only for development: DEV_AUTH_ENABLED must be true", file=sys.stderr)
        return 2
    with session_scope() as session:
        emails = seed(
            session,
            org_slug=arguments.org_slug,
            org_name=arguments.org_name,
            domain=arguments.domain,
        )
    print(f"organization {arguments.org_slug!r} ready; users: {', '.join(emails)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
