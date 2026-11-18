"""Real source-bound inbound ledger, v5 evaluation, retries and database defenses."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, delete, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError
from app.core.security import Principal, RoleCode
from app.models import AuditEvent, InboundEvidenceRevision
from app.schemas.inbound import (
    InboundContextRequest,
    InboundPosition,
    InboundProposal,
    InboundRecordRequest,
    InboundRevokeRequest,
    InboundSelection,
)
from app.schemas.purchasing_policies import PolicyDraftRequest, PurchasingPolicyDefinition
from app.schemas.recommendations import InboundRecommendationSnapshot, RecommendationPreviewRequest
from app.services import inbound as service
from app.services import purchasing_policies as policies
from app.services import recommendations
from tests.integration import test_purchasing_policies as managers
from tests.integration import test_recommendation_commercials as commercials
from tests.integration import test_recommendations as evaluations
from tests.integration.test_recommendations import isolated_engine as isolated_engine

pytestmark = pytest.mark.integration


def setup(session: Session, *, fbm: bool = False, total: int = 36) -> evaluations.Context:
    ctx = evaluations.setup(session)
    if fbm:
        ctx.stock.fbm_quantity = 10
        ctx.stock.raw = {
            "source": "listings_report",
            "listing": {
                "fulfillment-channel": "DEFAULT",
                "seller-sku": ctx.stock.seller_sku,
                "asin1": ctx.stock.asin,
                "quantity": "10",
            },
        }
        ctx.line.fulfillment_channel = "Merchant"
        current = policies.get_policy(session, ctx.policy.operator, ctx.policy_id)
        data = current.head.definition.model_dump(mode="json")
        data["amazon"]["stock_pools"][0]["channel"] = "FBM"
        draft = policies.record_draft(
            session,
            ctx.policy.operator,
            ctx.policy_id,
            PolicyDraftRequest(
                expected_head_id=current.head.id,
                definition=PurchasingPolicyDefinition.model_validate(data),
                reason="Synthetic reviewed merchant stock pool",
            ),
        )
        ctx.approval = managers.approve(session, ctx.policy, draft).head.id
    else:
        assert total % 6 == 0
        ctx.stock.inbound_shipped = total // 6
        ctx.stock.raw = {**ctx.stock.raw, "inbound_shipped": total // 6}
    session.commit()
    ctx.at = datetime.now(UTC)
    return ctx


def proposal(
    session: Session, ctx: evaluations.Context, total: int = 36, **changes: Any
) -> InboundProposal:
    pool = policies.get_policy(session, ctx.policy.operator, ctx.policy_id).approved
    assert pool is not None
    context = service.context(
        session,
        ctx.policy.manager,
        ctx.policy_id,
        InboundContextRequest(
            expected_approved_revision_id=ctx.approval,
            pool_id=pool.definition.amazon.stock_pools[0].pool_id,
        ),
    )
    assert not context.blockers, context.blockers
    at = datetime.now(UTC)
    position = InboundPosition.model_validate(
        {
            "declared_total_base_units": total,
            "arrivals": [
                {
                    "reference": "synthetic-shipment",
                    "base_units": total,
                    "expected_at": at + timedelta(days=1),
                }
            ]
            if total
            else [],
            "observed_at": at,
            "expires_at": at + timedelta(days=5),
            "evidence_reference": "Synthetic complete shipment manifest",
            "complete_position_confirmed": True,
            "no_on_hand_overlap_confirmed": True,
            **changes,
        }
    )
    return InboundProposal(
        expected_approved_revision_id=ctx.approval,
        pool_id=context.pool_id,
        expected_head_id=context.head_id,
        expected_source_sha256=context.source_sha256,
        position=position,
    )


def record_request(
    session: Session, ctx: evaluations.Context, proposed: InboundProposal
) -> InboundRecordRequest:
    checked = service.preview(session, ctx.policy.operator, ctx.policy_id, proposed)
    assert checked.can_confirm, checked.blockers
    return InboundRecordRequest(
        **proposed.model_dump(),
        request_id=uuid.uuid4(),
        expected_preview_sha256=checked.preview_sha256,
        reason="Synthetic verified complete source-bound position",
        acknowledge_review=True,
    )


def confirm(session: Session, ctx: evaluations.Context, total: int = 36, **changes: Any) -> Any:
    return service.record(
        session,
        ctx.policy.manager,
        ctx.policy_id,
        record_request(session, ctx, proposal(session, ctx, total, **changes)),
    )


def evaluate(
    session: Session, ctx: evaluations.Context, identifier: uuid.UUID | None = None, **changes: Any
) -> Any:
    return recommendations.preview_recommendation(
        session,
        ctx.policy.operator,
        ctx.policy_id,
        RecommendationPreviewRequest(
            expected_approved_revision_id=ctx.approval,
            inbound=InboundSelection(revision_ids=[identifier]) if identifier else None,
        ),
        now=lambda: changes.get("at", datetime.now(UTC)),
    )


@pytest.mark.parametrize("fbm,total", [(False, 36), (False, 0), (True, 36), (True, 0)])
def test_explicit_confirmation_unblocks_only_inbound_and_preserves_history(
    db_session: Session, fbm: bool, total: int
) -> None:
    ctx = setup(db_session, fbm=fbm, total=total)
    original = evaluate(db_session, ctx)
    saved = confirm(db_session, ctx, total)
    assert evaluate(db_session, ctx).snapshot.rule_version == original.snapshot.rule_version
    result = evaluate(db_session, ctx, saved.id)
    assert isinstance(result.snapshot, InboundRecommendationSnapshot)
    s = result.snapshot
    assert s.readiness.rule_version == "amazon-readiness-v2" and s.readiness.confirmations_persisted
    assert s.readiness.status == "DATA_AVAILABLE"
    assert s.readiness.pools[0].usable_inbound_base_units == total
    assert s.readiness.pools[0].observed_inbound_base_units == (None if fbm else total)
    assert s.inbound_assessments[0].status == "APPLIED"
    assert s.pools[0].inputs.inbound_units == total
    ctx.at = datetime.now(UTC)
    payload = evaluations.request(result, inbound={"revision_ids": [str(saved.id)]})
    recorded = evaluations.record(db_session, ctx, payload)
    assert recorded.snapshot == s.model_copy(
        update={
            "evaluated_at": recorded.snapshot.evaluated_at,
            "readiness": recorded.snapshot.readiness,
        }
    )
    assert recorded.snapshot_sha256 == policies.digest(recorded.snapshot.model_dump(mode="json"))
    assert evaluations.record(db_session, ctx, payload).id == recorded.id
    events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == saved.id)))
    assert len(events) == 1 and events[0].action == "inbound_evidence.confirmed"
    assert not recorded.purchasing_eligible and not saved.purchasing_eligible


def test_native_fba_total_cannot_be_replaced_by_a_guessed_smaller_position(
    db_session: Session,
) -> None:
    ctx = setup(db_session)
    checked = service.preview(
        db_session, ctx.policy.manager, ctx.policy_id, proposal(db_session, ctx, 30)
    )
    assert not checked.can_confirm and "FBA_INBOUND_TOTAL_MISMATCH" in checked.blockers
    payload = InboundRecordRequest(
        **checked.proposal.model_dump(),
        request_id=uuid.uuid4(),
        expected_preview_sha256=checked.preview_sha256,
        reason="Synthetic forbidden guessed lower inbound total",
        acknowledge_review=True,
    )
    with pytest.raises(service.InboundConflict):
        service.record(db_session, ctx.policy.manager, ctx.policy_id, payload)


@pytest.mark.parametrize("fault", ["source", "head", "digest", "ack"])
def test_confirmation_race_or_incomplete_ack_requires_new_review(
    db_session: Session, fault: str
) -> None:
    ctx = setup(db_session)
    proposed = proposal(db_session, ctx)
    payload = record_request(db_session, ctx, proposed)
    if fault == "source":
        ctx.stock.raw = {**ctx.stock.raw, "synthetic_revision": 2}
        db_session.commit()
    elif fault == "head":
        confirm(db_session, ctx)
    elif fault == "digest":
        payload = payload.model_copy(update={"expected_preview_sha256": "0" * 64})
    else:
        payload = payload.model_copy(update={"acknowledge_review": False})
    with pytest.raises(service.InboundConflict):
        service.record(db_session, ctx.policy.manager, ctx.policy_id, payload)


@pytest.mark.parametrize("fault", ["source", "expiry", "missed"])
def test_selected_evidence_becomes_unknown_on_source_change_expiry_or_missed_eta(
    db_session: Session, fault: str
) -> None:
    ctx = setup(db_session)
    saved = confirm(db_session, ctx)
    at = datetime.now(UTC)
    if fault == "source":
        ctx.stock.raw = {**ctx.stock.raw, "synthetic_revision": 2}
        db_session.commit()
    elif fault == "expiry":
        at = saved.position.expires_at
    else:
        at = saved.position.arrivals[0].expected_at
    result = evaluate(db_session, ctx, saved.id, at=at)
    assert result.snapshot.readiness.status == "BLOCKED"
    assert result.snapshot.readiness.pools[0].usable_inbound_base_units is None
    assessed = result.snapshot.inbound_assessments[0]
    assert assessed.status == "BLOCKED" and assessed.usable_base_units is None
    assert all(a.credited_units is None for a in assessed.arrivals)


def test_known_late_arrival_credits_zero_without_claiming_receipt(db_session: Session) -> None:
    ctx = setup(db_session)
    saved = confirm(
        db_session,
        ctx,
        arrivals=[
            {
                "reference": "late-shipment",
                "base_units": 36,
                "expected_at": ctx.stock.captured_at + timedelta(days=10),
            }
        ],
    )
    result = evaluate(db_session, ctx, saved.id)
    a = result.snapshot.inbound_assessments[0]
    assert a.status == "APPLIED" and a.usable_base_units == 0
    assert a.arrivals[0].disposition == "AFTER_STOCKOUT"
    evaluations.record(
        db_session, ctx, evaluations.request(result, inbound={"revision_ids": [str(saved.id)]})
    )


def test_retry_is_exact_revocation_is_a_new_head_and_old_evaluations_replay(
    db_session: Session,
) -> None:
    ctx = setup(db_session)
    request = record_request(db_session, ctx, proposal(db_session, ctx))
    saved = service.record(db_session, ctx.policy.manager, ctx.policy_id, request)
    result = evaluate(db_session, ctx, saved.id)
    ctx.at = datetime.now(UTC)
    evaluation_request = evaluations.request(result, inbound={"revision_ids": [str(saved.id)]})
    evaluation = evaluations.record(db_session, ctx, evaluation_request)
    revoke_request = InboundRevokeRequest(
        request_id=uuid.uuid4(),
        expected_head_id=saved.id,
        reason="Synthetic shipment evidence withdrawn after review",
        acknowledge_review=True,
    )
    revoked = service.revoke(db_session, ctx.policy.manager, ctx.policy_id, revoke_request)
    assert revoked.kind == "REVOKED" and revoked.previous_revision_id == saved.id
    assert revoked.position == saved.position
    assert service.revoke(db_session, ctx.policy.manager, ctx.policy_id, revoke_request) == revoked
    assert service.record(db_session, ctx.policy.manager, ctx.policy_id, request) == saved
    assert evaluations.record(db_session, ctx, evaluation_request) == evaluation
    with pytest.raises(service.InboundConflict):
        evaluate(db_session, ctx, saved.id)
    assert service.page(db_session, ctx.policy.operator, ctx.policy_id, 1, 25).items == (revoked,)
    assert (
        service.page(db_session, ctx.policy.operator, ctx.policy_id, 1, 1, heads_only=False).total
        == 2
    )
    with pytest.raises(service.InboundConflict):
        service.record(
            db_session,
            ctx.policy.manager,
            ctx.policy_id,
            request.model_copy(update={"reason": "Changed reason with reused request identifier"}),
        )


@pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.DATA_OPERATOR])
def test_readers_cannot_confirm_or_revoke(db_session: Session, role: RoleCode) -> None:
    ctx = setup(db_session)
    payload = record_request(db_session, ctx, proposal(db_session, ctx))
    actor = Principal(
        ctx.policy.operator.user_id,
        ctx.policy.org.id,
        ctx.policy.operator.email,
        "Synthetic reader",
        frozenset({role}),
    )
    assert service.preview(
        db_session,
        actor,
        ctx.policy_id,
        InboundProposal.model_validate(
            payload.model_dump(include=set(InboundProposal.model_fields))
        ),
    ).can_confirm
    with pytest.raises(AuthorizationError):
        service.record(db_session, actor, ctx.policy_id, payload)
    with pytest.raises(AuthorizationError):
        service.revoke(
            db_session,
            actor,
            ctx.policy_id,
            InboundRevokeRequest(
                request_id=uuid.uuid4(),
                expected_head_id=uuid.uuid4(),
                reason="Synthetic unauthorized revoke attempt",
                acknowledge_review=True,
            ),
        )


def test_tenant_and_policy_isolation(db_session: Session) -> None:
    ctx = setup(db_session)
    saved = confirm(db_session, ctx)
    foreign = setup(db_session)
    with pytest.raises(service.InboundNotFound):
        service.page(db_session, foreign.policy.manager, ctx.policy_id, 1, 25)
    with pytest.raises(service.InboundNotFound):
        evaluate(db_session, foreign, saved.id)


def test_audit_failure_rolls_back_confirmation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = setup(db_session)
    request = record_request(db_session, ctx, proposal(db_session, ctx))

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("app.services.inbound.audit.record", fail)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        service.record(db_session, ctx.policy.manager, ctx.policy_id, request)
    assert not service.page(db_session, ctx.policy.operator, ctx.policy_id, 1, 25).items


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_ledger_cannot_be_mutated_in_database(db_session: Session, operation: str) -> None:
    ctx = setup(db_session)
    saved = confirm(db_session, ctx)
    statement = (
        update(InboundEvidenceRevision).values(reason="Tampered evidence")
        if operation == "update"
        else delete(InboundEvidenceRevision)
    )
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(statement.where(InboundEvidenceRevision.id == saved.id))


@pytest.mark.parametrize("fault", ["ownership", "total", "eta", "reference", "ack", "expiry"])
def test_database_rejects_forged_inbound_confirmation(db_session: Session, fault: str) -> None:
    ctx = setup(db_session)
    saved = confirm(db_session, ctx)
    row = db_session.get(InboundEvidenceRevision, saved.id)
    assert row is not None
    data = {
        c.name: getattr(row, c.name)
        for c in row.__table__.columns
        if c.name not in {"created_at", "updated_at"}
    }
    data.update(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        previous_revision_id=row.id,
        revision_number=2,
        recorded_at=datetime.now(UTC),
    )
    if fault == "ownership":
        data["product_id"] = uuid.uuid4()
    elif fault == "total":
        data["declared_total_base_units"] = 30
    elif fault == "expiry":
        data["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    elif fault == "ack":
        data["position"] = {**row.position, "complete_position_confirmed": False}
    else:
        arrival = {**row.position["arrivals"][0]}
        arrival["expected_at" if fault == "eta" else "reference"] = None
        data["position"] = {**row.position, "arrivals": [arrival]}
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(insert(InboundEvidenceRevision).values(**data))


def test_v5_composes_with_approved_commercials(db_session: Session) -> None:
    source = commercials.setup(db_session)
    ctx = source.calculation
    saved = confirm(db_session, ctx, 0)
    result = evaluate(db_session, ctx, saved.id)
    assert result.snapshot.rule_version == "replenishment-evaluation-v5"
    assert result.snapshot.commercial is not None and result.snapshot.allocation is not None
    ctx.at = datetime.now(UTC)
    evaluations.record(
        db_session, ctx, evaluations.request(result, inbound={"revision_ids": [str(saved.id)]})
    )


def test_concurrent_exact_retries_insert_one_revision(isolated_engine: Engine) -> None:
    with Session(isolated_engine, expire_on_commit=False) as session:
        ctx = setup(session)
        payload = record_request(session, ctx, proposal(session, ctx))
        session.commit()

    def save(_: int) -> uuid.UUID:
        with Session(isolated_engine) as session:
            return service.record(session, ctx.policy.manager, ctx.policy_id, payload).id

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert len(set(workers.map(save, [1, 2]))) == 1


def test_downgrade_refuses_reviewed_evidence(isolated_engine: Engine) -> None:
    from tests.integration.conftest import downgrade_migrations

    with Session(isolated_engine) as session:
        ctx = setup(session)
        confirm(session, ctx)
    with pytest.raises(DBAPIError, match="Cannot discard reviewed inbound evidence"):
        downgrade_migrations(isolated_engine.url, "a61117202608")
