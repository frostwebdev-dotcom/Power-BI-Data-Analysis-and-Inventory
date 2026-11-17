"""Read-only product lookup for catalogue browsing and explicit selections."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.v1.routes.vendors import READ_ROLES
from app.core.errors import NotFoundError
from app.core.security import Principal
from app.db.session import get_db
from app.models.catalog import Product
from app.models.enums import Marketplace
from app.repositories.products import ProductRepository
from app.schemas.products import (
    CataloguePage,
    IdentifierResponse,
    ListingResponse,
    ProductResponse,
    VendorProductChoice,
)
from app.services.products import listing_response

router = APIRouter(prefix="/products", tags=["products"])
_read = Depends(require_roles(*READ_ROLES))


def _product(repository: ProductRepository, product_id: uuid.UUID) -> Product:
    found = repository.get(product_id)
    if found is None:
        raise NotFoundError("No such product in this organization.")
    return found


@router.get("", response_model=CataloguePage[ProductResponse], summary="Search the catalogue")
def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    q: str | None = Query(None, max_length=200),
    include_inactive: bool = False,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> CataloguePage[ProductResponse]:
    result = ProductRepository(session, principal.organization_id).list(
        page=page, page_size=page_size, q=q, include_inactive=include_inactive
    )
    return CataloguePage(
        items=[ProductResponse.model_validate(p) for p in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/{product_id}", response_model=ProductResponse, summary="Product details")
def get_product(
    product_id: uuid.UUID,
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> ProductResponse:
    return ProductResponse.model_validate(
        _product(ProductRepository(session, principal.organization_id), product_id)
    )


@router.get("/{product_id}/identifiers", response_model=CataloguePage[IdentifierResponse])
def list_identifiers(
    product_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> CataloguePage[IdentifierResponse]:
    repository = ProductRepository(session, principal.organization_id)
    _product(repository, product_id)
    result = repository.identifiers(product_id, page, page_size)
    return CataloguePage(
        items=[IdentifierResponse.model_validate(p) for p in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/{product_id}/listings", response_model=CataloguePage[ListingResponse])
def list_listings(
    product_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    marketplace: Marketplace | None = Query(None),
    marketplace_id: str | None = Query(None, max_length=32),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> CataloguePage[ListingResponse]:
    repository = ProductRepository(session, principal.organization_id)
    _product(repository, product_id)
    result = repository.listings(
        product_id, page, page_size, marketplace=marketplace, marketplace_id=marketplace_id
    )
    return CataloguePage(
        items=[listing_response(p) for p in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get(
    "/{product_id}/vendor-products",
    response_model=CataloguePage[VendorProductChoice],
    responses={403: {"description": "A staff role is required."}},
)
def vendor_product_choices(
    product_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    q: str | None = Query(None, max_length=200),
    principal: Principal = _read,
    session: Session = Depends(get_db),
) -> CataloguePage[VendorProductChoice]:
    repository = ProductRepository(session, principal.organization_id)
    _product(repository, product_id)
    rows, total = repository.vendor_products(product_id, page, page_size, q)
    return CataloguePage(
        items=[
            VendorProductChoice(
                id=row.id,
                product_id=product_id,
                vendor_id=vendor.id,
                vendor_name=vendor.name,
                vendor_code=vendor.code,
                vendor_sku=row.vendor_sku,
                is_active=row.is_active,
                vendor_is_active=vendor.is_active,
                mapping_status=row.mapping_status,
                mapping_method=row.mapping_method,
                approved_by_user_id=row.mapping_approved_by_user_id,
                approved_at=row.mapping_approved_at,
            )
            for row, vendor in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )
