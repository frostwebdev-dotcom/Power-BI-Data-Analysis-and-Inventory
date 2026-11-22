"""Deterministic binding proposals, explicit reasons and early role rejection."""

from __future__ import annotations

import uuid
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.schemas.financial_imports import FinancialBindingEvidence, FinancialBindingRecordRequest
from app.services.financial_bindings import (
    bindings_digest,
    preview_binding_revision,
    record_binding_revision,
)


def evidence() -> FinancialBindingEvidence:
    return FinancialBindingEvidence(
        mapping_status="UNRESOLVED",
        listing_id=None,
        product_id=None,
        match_priority=None,
        mapping_snapshot={},
        review_reasons=["MAPPING_UNRESOLVED"],
        review_required=True,
    )


def test_digest_is_order_independent_and_captures_head_source_and_approval_evidence() -> None:
    identifier, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [(first, evidence()), (second, evidence())]
    digest = bindings_digest(identifier, None, rows)
    assert len(digest) == 64 and digest == bindings_digest(identifier, None, list(reversed(rows)))
    assert digest != bindings_digest(uuid.uuid4(), None, rows)
    assert digest != bindings_digest(identifier, uuid.uuid4(), rows)
    assert digest != bindings_digest(identifier, None, rows[:1])
    revised = evidence().model_copy(
        update={"mapping_snapshot": {"approved_at": "2026-11-17T00:00:00Z"}}
    )
    assert digest != bindings_digest(identifier, None, [(first, revised), rows[1]])
    assert digest != bindings_digest(
        identifier,
        None,
        [
            (
                first,
                evidence().model_copy(update={"review_reasons": ["ZERO_COST_WITH_POSITIVE_SALES"]}),
            ),
            rows[1],
        ],
    )


@pytest.mark.parametrize(
    "reason", ["", "  ", "too short", "x" * 1001, "a useful\nreason", "a useful\x7freason"]
)
def test_record_requires_a_meaningful_bounded_reason(reason: str) -> None:
    with pytest.raises(ValidationError):
        FinancialBindingRecordRequest(
            expected_revision_id=None, bindings_sha256="a" * 64, reason=reason
        )


def test_record_requires_explicit_head_and_valid_digest_and_refuses_source_edits() -> None:
    request = FinancialBindingRecordRequest(
        expected_revision_id=None,
        bindings_sha256="a" * 64,
        reason="  Apply newly approved SKU links.  ",
    )
    assert request.reason == "Apply newly approved SKU links."
    for changes in ({"bindings_sha256": "wrong"}, {"cost": "0"}, {"reporting_year": 2026}):
        with pytest.raises(ValidationError):
            FinancialBindingRecordRequest.model_validate({**request.model_dump(), **changes})
    with pytest.raises(ValidationError):
        FinancialBindingRecordRequest.model_validate(
            {"bindings_sha256": "a" * 64, "reason": "Apply approved links"}
        )


@pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.PURCHASING_MANAGER])
def test_non_operator_cannot_preview_or_record_even_through_service(role: RoleCode) -> None:
    actor = Principal(
        uuid.uuid4(), uuid.uuid4(), "reader@example.test", "Reader", frozenset({role})
    )
    session = cast(Session, object())
    with pytest.raises(AuthorizationError):
        preview_binding_revision(session, actor, uuid.uuid4(), 1, 25)
    with pytest.raises(AuthorizationError):
        record_binding_revision(
            session,
            actor,
            uuid.uuid4(),
            FinancialBindingRecordRequest(
                expected_revision_id=None, bindings_sha256="a" * 64, reason="Apply approved links"
            ),
        )
