"""Explicit current observations and separate urgency/supplier-review signals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.purchasing_policies import PolicyIdentity
from app.schemas.recommendations import EvaluationSnapshot
from app.schemas.replenishment import PreviewModel
from app.schemas.replenishment_review import ComparisonChoice

Urgency = Literal["URGENT", "REPLENISH", "NO_REORDER", "UNKNOWN"]
Opportunity = Literal["REVIEW_OPPORTUNITY", "UNAVAILABLE", "UNCONFIRMED", "NO_SHORTFALL"]


class CurrentCheckRequest(PreviewModel):
    choices: Annotated[tuple[ComparisonChoice, ...], Field(min_length=1, max_length=10)]

    @model_validator(mode="after")
    def distinct_choices(self) -> CurrentCheckRequest:
        if len({choice.policy_id for choice in self.choices}) != len(self.choices):
            raise ValueError("Select each policy only once.")
        return self


class CurrentPolicyChoice(PreviewModel):
    policy_id: uuid.UUID
    product_id: uuid.UUID
    approved_revision_id: uuid.UUID
    approved_revision_number: int
    identity: PolicyIdentity


class CurrentChoicePage(PreviewModel):
    items: tuple[CurrentPolicyChoice, ...]
    page: int
    page_size: int
    total: int


class CurrentPoolSignal(PreviewModel):
    pool_id: str
    channel: str
    urgency: Urgency
    sales_base_units: int | None
    on_hand_base_units: int | None
    usable_inbound_base_units: int | None
    window_days: int
    lead_plus_safety_days: int
    required_units: int | None
    net_shortfall_units: int | None
    supply_days_numerator: str | None
    supply_days_denominator: str | None
    reasons: tuple[str, ...]


class CurrentPolicyCheck(PreviewModel):
    identity: PolicyIdentity
    policy_id: uuid.UUID
    product_id: uuid.UUID
    approved_revision_id: uuid.UUID
    urgency: Urgency
    opportunity: Opportunity
    reasons: tuple[str, ...]
    pools: tuple[CurrentPoolSignal, ...]
    snapshot: EvaluationSnapshot
    evidence_sha256: str
    purchasing_eligible: Literal[False] = False


class CurrentCheckSummary(PreviewModel):
    checked_policies: int
    urgent: int
    replenish: int
    no_reorder: int
    unknown: int
    review_opportunities: int


class CurrentCheckResponse(PreviewModel):
    rule_version: Literal["current-replenishment-v1"] = "current-replenishment-v1"
    evaluated_at: datetime
    scope: Literal["EXPLICITLY_SELECTED_APPROVED_POLICIES"] = (
        "EXPLICITLY_SELECTED_APPROVED_POLICIES"
    )
    summary: CurrentCheckSummary
    items: tuple[CurrentPolicyCheck, ...]
    freshness_rechecked: Literal[True] = True
    live_sync_performed: Literal[False] = False
    saved: Literal[False] = False
    stock_reserved: Literal[False] = False
    purchasing_eligible: Literal[False] = False
