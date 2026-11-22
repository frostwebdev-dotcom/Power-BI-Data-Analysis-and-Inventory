"""Exact, conservative arrival qualification anchored to observed inventory."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.schemas.inbound import InboundPosition, QualifiedArrival


def microseconds(delta: timedelta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def qualify_arrivals(
    position: InboundPosition,
    captured_at: datetime | None,
    sales: int | None,
    on_hand: int | None,
    window_days: int,
    coverage_days: int,
    at: datetime,
) -> tuple[int | None, tuple[QualifiedArrival, ...], tuple[str, ...]]:
    reasons = set()
    if not position.complete_position_confirmed or not position.no_on_hand_overlap_confirmed:
        reasons.add("INBOUND_POSITION_NOT_ATTESTED")
    if captured_at is None or sales is None or on_hand is None:
        reasons.add("INBOUND_STOCK_OR_DEMAND_UNCONFIRMED")
    if position.observed_at > at or (
        captured_at is not None and position.observed_at < captured_at
    ):
        reasons.add("INBOUND_OBSERVATION_UNCONFIRMED")
    if position.expires_at <= at:
        reasons.add("INBOUND_EVIDENCE_EXPIRED")
    if any(a.expected_at <= at for a in position.arrivals):
        reasons.add("INBOUND_ARRIVAL_MISSED_REQUIRES_RECONCILIATION")
    arrivals = sorted(position.arrivals, key=lambda a: (a.expected_at, a.reference))
    if reasons:
        return (
            None,
            tuple(
                QualifiedArrival(
                    reference=a.reference,
                    base_units=a.base_units,
                    expected_at=a.expected_at,
                    disposition="UNCONFIRMED",
                    credited_units=None,
                )
                for a in arrivals
            ),
            tuple(sorted(reasons)),
        )
    assert captured_at is not None and sales is not None and on_hand is not None
    horizon = captured_at + timedelta(days=coverage_days)
    usable = 0
    assessed = []
    for arrival in arrivals:
        within = arrival.expected_at <= horizon
        before_stockout = sales == 0 or (
            on_hand + usable
        ) * window_days * 86400 * 1000000 > sales * microseconds(arrival.expected_at - captured_at)
        credit = arrival.base_units if within and before_stockout else 0
        assessed.append(
            QualifiedArrival(
                reference=arrival.reference,
                base_units=arrival.base_units,
                expected_at=arrival.expected_at,
                disposition="AFTER_COVERAGE"
                if not within
                else "BEFORE_STOCKOUT"
                if before_stockout
                else "AFTER_STOCKOUT",
                credited_units=credit,
            )
        )
        usable += credit
    return usable, tuple(assessed), ()
