"""Strict complete positions and exact sequential arrival qualification."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.inbound import InboundPosition
from app.services.inbound_math import qualify_arrivals

AT = datetime(2026, 11, 18, tzinfo=UTC)


def position(total: int = 10, *, days: float = 1, **changes: Any) -> InboundPosition:
    data = {
        "declared_total_base_units": total,
        "arrivals": [
            {
                "reference": "shipment-1",
                "base_units": total,
                "expected_at": AT + timedelta(days=days),
            }
        ]
        if total
        else [],
        "observed_at": AT,
        "expires_at": AT + timedelta(days=5),
        "evidence_reference": "Synthetic complete shipment position",
        "complete_position_confirmed": True,
        "no_on_hand_overlap_confirmed": True,
    }
    return InboundPosition.model_validate({**data, **changes})


def qualify(p: InboundPosition, **changes: Any) -> Any:
    return qualify_arrivals(
        p,
        **{
            "captured_at": AT - timedelta(minutes=1),
            "sales": 14,
            "on_hand": 10,
            "window_days": 14,
            "coverage_days": 30,
            "at": AT,
            **changes,
        },
    )


@pytest.mark.parametrize("invalid", [None, True, "10", 10.5, -1, 9223372036854775808])
def test_declared_position_has_no_guess_or_coercion(invalid: Any) -> None:
    with pytest.raises(ValidationError):
        position(declared_total_base_units=invalid)


@pytest.mark.parametrize(
    "changes",
    [
        {"arrivals": []},
        {"observed_at": "2026-11-18T00:00:00"},
        {"expires_at": "2026-11-19T00:00:00"},
        {"expires_at": AT},
        {"expires_at": AT + timedelta(days=32)},
        {"evidence_reference": "          "},
        {"complete_position_confirmed": "true"},
        {"no_on_hand_overlap_confirmed": 1},
        {"unknown": True},
    ],
)
def test_position_requires_complete_explicit_evidence(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        position(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"base_units": 0},
        {"base_units": True},
        {"base_units": "10"},
        {"expected_at": "2026-11-19T00:00:00"},
        {"reference": " "},
    ],
)
def test_arrival_is_positive_whole_timezone_aware_and_referenced(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        position(
            arrivals=[
                {
                    "reference": "shipment-1",
                    "base_units": 10,
                    "expected_at": AT + timedelta(days=1),
                    **changes,
                }
            ]
        )


def test_case_insensitive_duplicate_references_rejected() -> None:
    with pytest.raises(ValidationError):
        position(
            arrivals=[
                {"reference": r, "base_units": 5, "expected_at": AT + timedelta(days=1)}
                for r in ["SHP-1", "shp-1"]
            ]
        )


def test_explicit_zero_is_usable_but_never_defaulted() -> None:
    assert qualify(position(0)) == (0, (), ())
    with pytest.raises(ValidationError):
        InboundPosition.model_validate({})


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"complete_position_confirmed": False}, "INBOUND_POSITION_NOT_ATTESTED"),
        ({"no_on_hand_overlap_confirmed": False}, "INBOUND_POSITION_NOT_ATTESTED"),
        ({"observed_at": AT + timedelta(seconds=1)}, "INBOUND_OBSERVATION_UNCONFIRMED"),
        ({"observed_at": AT - timedelta(days=1)}, "INBOUND_OBSERVATION_UNCONFIRMED"),
        ({"observed_at": AT - timedelta(days=1), "expires_at": AT}, "INBOUND_EVIDENCE_EXPIRED"),
        ({"days": 0}, "INBOUND_ARRIVAL_MISSED_REQUIRES_RECONCILIATION"),
    ],
)
def test_unconfirmed_position_never_credits_partial_units(
    changes: dict[str, Any], reason: str
) -> None:
    total, arrivals, reasons = qualify(position(**changes))
    assert total is None and reason in reasons
    assert all(a.credited_units is None and a.disposition == "UNCONFIRMED" for a in arrivals)


@pytest.mark.parametrize("changes", [{"captured_at": None}, {"sales": None}, {"on_hand": None}])
def test_missing_stock_or_demand_is_unknown(changes: dict[str, Any]) -> None:
    assert qualify(position(), **changes)[0] is None


def test_exact_boundary_one_microsecond_before_stockout_only() -> None:
    # Ten days of supply; float rounding at the boundary would credit incorrectly.
    boundary = AT + timedelta(days=10)
    for delta, expected in [(-1, 10), (0, 0), (1, 0)]:
        p = position(
            arrivals=[
                {
                    "reference": "boundary",
                    "base_units": 10,
                    "expected_at": boundary + timedelta(microseconds=delta),
                }
            ]
        )
        total, arrivals, _ = qualify(p, captured_at=AT)
        assert total == expected
        assert arrivals[0].disposition == ("BEFORE_STOCKOUT" if expected else "AFTER_STOCKOUT")


def test_earlier_confirmed_arrival_bridges_a_later_arrival() -> None:
    p = position(
        20,
        arrivals=[
            {"reference": "second", "base_units": 10, "expected_at": AT + timedelta(days=15)},
            {"reference": "first", "base_units": 10, "expected_at": AT + timedelta(days=5)},
        ],
    )
    total, arrivals, reasons = qualify(p, captured_at=AT)
    assert total == 20 and not reasons
    assert [a.reference for a in arrivals] == ["first", "second"]
    assert all(a.disposition == "BEFORE_STOCKOUT" for a in arrivals)


def test_late_arrival_does_not_bridge_stockout_or_credit_later_stock() -> None:
    p = position(
        20,
        arrivals=[
            {"reference": "late", "base_units": 10, "expected_at": AT + timedelta(days=10)},
            {"reference": "later", "base_units": 10, "expected_at": AT + timedelta(days=15)},
        ],
    )
    total, arrivals, _ = qualify(p, captured_at=AT)
    assert total == 0 and all(a.disposition == "AFTER_STOCKOUT" for a in arrivals)


def test_beyond_coverage_is_known_zero_credit_and_large_values_are_exact() -> None:
    p = position(9223372036854775807, days=31)
    assert qualify(p, sales=0)[0] == 0
    assert qualify(p, sales=0)[1][0].disposition == "AFTER_COVERAGE"
    assert qualify(position(9223372036854775807), sales=0)[0] == 9223372036854775807
