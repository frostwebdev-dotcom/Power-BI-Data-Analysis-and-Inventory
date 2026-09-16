"""The deterministic matching engine (CLAUDE.md §5.1, §5.2; AC-7).

Every case is named for the rule it exercises. The engine is pure, so the
lookups are an in-memory ``MatchIndex`` and nothing else is involved.
"""

from __future__ import annotations

import uuid

import pytest

from app.matching.engine import (
    SUGGESTION_RULE,
    MatchIndex,
    MatchInput,
    Suggestion,
    evaluate,
    normalize_catalog_item_number,
)
from app.models.enums import MatchMethod, MatchResult

VENDOR = uuid.UUID("00000000-0000-0000-0000-00000000000a")
P1 = uuid.UUID("00000000-0000-0000-0000-000000000001")
P2 = uuid.UUID("00000000-0000-0000-0000-000000000002")
P3 = uuid.UUID("00000000-0000-0000-0000-000000000003")
UPC = "00012345678905"


def index(**kwargs: object) -> MatchIndex:
    return MatchIndex(**kwargs)  # type: ignore[arg-type]


def trail(outcome: object) -> list[tuple[str, int, str]]:
    return [(e.rule, e.priority, e.outcome) for e in outcome.evaluations]  # type: ignore[attr-defined]


# --- priority 1: exact normalised UPC ------------------------------------------------------


class TestRule1Upc:
    def test_exact_upc_matches_at_priority_1(self) -> None:
        outcome = evaluate(
            MatchInput(normalized_upc=UPC, upc_valid_checksum=True), index(by_upc={UPC: (P1,)})
        )

        assert outcome.result is MatchResult.MATCHED
        assert (outcome.product_id, outcome.method, outcome.priority) == (P1, MatchMethod.UPC, 1)
        assert trail(outcome) == [
            ("UPC", 1, "matched"),
            ("CATALOG_ITEM_NUMBER", 2, "skipped"),
            ("VENDOR_SKU_MAPPING", 3, "skipped"),
            ("AMAZON_SKU_MAPPING", 4, "skipped"),
        ]
        assert outcome.evaluations[1].note == "an earlier rule matched"

    def test_a_upc_matching_two_products_is_ambiguous_even_though_the_sku_would_match(
        self,
    ) -> None:
        """The defining case: a stronger rule's ambiguity is never settled by a
        weaker rule, and no candidate is picked (AC-7.4)."""
        lookups = index(
            by_upc={UPC: (P1, P2)},
            by_vendor_sku={(VENDOR, "SKU-1"): (P1,)},
        )

        outcome = evaluate(
            MatchInput(
                normalized_upc=UPC,
                upc_valid_checksum=True,
                vendor_id=VENDOR,
                normalized_vendor_sku="SKU-1",
            ),
            lookups,
        )

        assert outcome.result is MatchResult.AMBIGUOUS
        assert outcome.priority == 1 and outcome.product_id is None
        assert outcome.candidates == (P1, P2)
        assert trail(outcome) == [
            ("UPC", 1, "ambiguous"),
            ("CATALOG_ITEM_NUMBER", 2, "skipped"),
            ("VENDOR_SKU_MAPPING", 3, "skipped"),
            ("AMAZON_SKU_MAPPING", 4, "skipped"),
        ]
        assert outcome.evaluations[2].note == "an earlier rule was ambiguous"

    def test_a_upc_with_a_bad_check_digit_is_not_an_identifier(self) -> None:
        outcome = evaluate(
            MatchInput(normalized_upc=UPC, upc_valid_checksum=False, description="x"),
            index(by_upc={UPC: (P1,)}),
        )

        assert outcome.result is MatchResult.UNMATCHED
        assert outcome.evaluations[0].outcome == "skipped"
        assert "check digit" in (outcome.evaluations[0].note or "")

    def test_a_upc_the_catalog_does_not_know_falls_through(self) -> None:
        lookups = index(by_upc={"00099999999990": (P1,)}, by_catalog_item_number={"CIN-1": (P2,)})

        outcome = evaluate(
            MatchInput(normalized_upc=UPC, upc_valid_checksum=True, catalog_item_number="CIN-1"),
            lookups,
        )

        assert outcome.result is MatchResult.MATCHED and outcome.product_id == P2
        assert trail(outcome)[:2] == [("UPC", 1, "no_match"), ("CATALOG_ITEM_NUMBER", 2, "matched")]


# --- priority 2: exact catalog item number -----------------------------------------------


class TestRule2CatalogItemNumber:
    def test_exact_catalog_number_matches_at_priority_2(self) -> None:
        outcome = evaluate(
            MatchInput(catalog_item_number=" cin-42 "),
            index(by_catalog_item_number={"CIN-42": (P1,)}),
        )

        assert outcome.result is MatchResult.MATCHED
        assert (outcome.method, outcome.priority, outcome.product_id) == (
            MatchMethod.CATALOG_ITEM_NUMBER,
            2,
            P1,
        )
        assert outcome.evaluations[1].input == "CIN-42"

    def test_normalisation_is_trim_and_case_only(self) -> None:
        assert normalize_catalog_item_number("  ab  12 ") == "AB 12"
        assert normalize_catalog_item_number("ab-12") != normalize_catalog_item_number("ab12")


# --- priority 3: approved vendor SKU mapping ----------------------------------------------


class TestRule3VendorSkuMapping:
    def test_an_approved_vendor_sku_is_reused_on_the_second_import(self) -> None:
        """First import: no identifiers, no mapping → nothing. After approval the
        same SKU resolves through priority 3 with the same input."""
        candidate = MatchInput(vendor_id=VENDOR, normalized_vendor_sku="ACM-001")

        before = evaluate(candidate, index())
        after = evaluate(candidate, index(by_vendor_sku={(VENDOR, "ACM-001"): (P1,)}))

        assert before.result is MatchResult.UNMATCHED
        assert after.result is MatchResult.MATCHED
        assert (after.method, after.priority, after.product_id) == (
            MatchMethod.VENDOR_SKU_MAPPING,
            3,
            P1,
        )
        assert trail(after) == [
            ("UPC", 1, "skipped"),
            ("CATALOG_ITEM_NUMBER", 2, "skipped"),
            ("VENDOR_SKU_MAPPING", 3, "matched"),
            ("AMAZON_SKU_MAPPING", 4, "skipped"),
        ]

    def test_another_vendors_mapping_is_not_consulted(self) -> None:
        other = uuid.uuid4()
        outcome = evaluate(
            MatchInput(vendor_id=VENDOR, normalized_vendor_sku="ACM-001"),
            index(by_vendor_sku={(other, "ACM-001"): (P1,)}),
        )
        assert outcome.result is MatchResult.UNMATCHED


# --- priority 4: approved Amazon SKU mapping ----------------------------------------------


class TestRule4AmazonSkuMapping:
    def test_an_approved_amazon_sku_matches_at_priority_4(self) -> None:
        outcome = evaluate(
            MatchInput(amazon_seller_sku=" AMZ-1 "), index(by_amazon_sku={"AMZ-1": (P3,)})
        )

        assert outcome.result is MatchResult.MATCHED
        assert (outcome.method, outcome.priority, outcome.product_id) == (
            MatchMethod.AMAZON_SKU_MAPPING,
            4,
            P3,
        )
        assert outcome.evaluations[3].input == "AMZ-1"

    def test_an_ambiguous_amazon_sku_is_ambiguous_at_priority_4(self) -> None:
        outcome = evaluate(
            MatchInput(amazon_seller_sku="AMZ-1", description="Blue Widget"),
            index(by_amazon_sku={"AMZ-1": (P1, P3)}, suggester=lambda _d: [Suggestion(P2, 0.9)]),
        )
        assert outcome.result is MatchResult.AMBIGUOUS and outcome.priority == 4
        assert outcome.suggestions == ()  # rule 5 never runs after an ambiguity


# --- priority 5: suggestion only ------------------------------------------------------------


class TestRule5Suggestion:
    def test_a_description_identical_to_a_product_name_is_a_suggestion_never_a_match(
        self,
    ) -> None:
        lookups = index(suggester=lambda d: [Suggestion(P1, 1.0)] if d == "Blue Widget" else [])

        outcome = evaluate(MatchInput(description="Blue Widget"), lookups)

        assert outcome.result is MatchResult.SUGGESTION_ONLY
        assert outcome.product_id is None and outcome.method is None
        assert outcome.priority == 5 and outcome.candidates == (P1,)
        assert outcome.suggestions == (Suggestion(P1, 1.0),)
        assert trail(outcome)[-1] == (SUGGESTION_RULE, 5, "suggested")
        assert outcome.candidates_json()["products"] == [
            {
                "product_id": str(P1),
                "rule": SUGGESTION_RULE,
                "priority": 5,
                "score": 1.0,
                "reason": "name_similarity",
            }
        ]

    def test_suggestions_are_ranked_capped_and_thresholded(self) -> None:
        raw = [Suggestion(P2, 0.5), Suggestion(P1, 0.9), Suggestion(P3, 0.2)] + [
            Suggestion(uuid.uuid4(), 0.4) for _ in range(6)
        ]

        outcome = evaluate(MatchInput(description="x"), index(suggester=lambda _d: raw))

        assert len(outcome.suggestions) == 5
        assert outcome.suggestions[0].product_id == P1 and outcome.suggestions[1].product_id == P2
        assert all(s.score >= 0.3 for s in outcome.suggestions)

    def test_rule_5_does_not_run_when_an_identifier_matched(self) -> None:
        calls: list[str] = []

        def suggester(description: str) -> list[Suggestion]:
            calls.append(description)
            return [Suggestion(P2, 1.0)]

        outcome = evaluate(
            MatchInput(normalized_upc=UPC, upc_valid_checksum=True, description="Blue Widget"),
            index(by_upc={UPC: (P1,)}, suggester=suggester),
        )

        assert outcome.result is MatchResult.MATCHED and calls == []


# --- nothing at all -------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_input_is_unmatched_with_four_evaluations_recorded(self) -> None:
        outcome = evaluate(MatchInput(), index())

        assert outcome.result is MatchResult.UNMATCHED
        assert outcome.product_id is None and outcome.candidates == ()
        assert [(e.rule, e.outcome) for e in outcome.evaluations[:4]] == [
            ("UPC", "skipped"),
            ("CATALOG_ITEM_NUMBER", "skipped"),
            ("VENDOR_SKU_MAPPING", "skipped"),
            ("AMAZON_SKU_MAPPING", "skipped"),
        ]
        assert [e.note for e in outcome.evaluations[:4]] == [
            "no UPC",
            "no catalog item number",
            "no vendor SKU",
            "no Amazon seller SKU",
        ]
        assert outcome.evaluations[4].rule == SUGGESTION_RULE
        assert outcome.evaluations[4].note == "no description"

    def test_a_description_that_resembles_nothing_is_unmatched(self) -> None:
        outcome = evaluate(MatchInput(description="Mystery"), index(suggester=lambda _d: []))
        assert outcome.result is MatchResult.UNMATCHED
        assert trail(outcome)[-1] == (SUGGESTION_RULE, 5, "no_match")


# --- determinism ------------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize(
        "candidate",
        [
            MatchInput(normalized_upc=UPC, upc_valid_checksum=True),
            MatchInput(normalized_upc=UPC, upc_valid_checksum=True, normalized_vendor_sku="S"),
            MatchInput(vendor_id=VENDOR, normalized_vendor_sku="ACM-001"),
            MatchInput(description="Blue Widget"),
            MatchInput(),
        ],
    )
    def test_the_same_input_against_the_same_index_gives_the_same_outcome(
        self, candidate: MatchInput
    ) -> None:
        lookups = index(
            by_upc={UPC: (P1, P2)},
            by_vendor_sku={(VENDOR, "ACM-001"): (P3,)},
            suggester=lambda _d: [Suggestion(P1, 0.8), Suggestion(P2, 0.8)],
        )

        first = evaluate(candidate, lookups)
        second = evaluate(candidate, lookups)

        assert first == second
        assert first.as_json() == second.as_json()

    def test_candidates_are_sorted_regardless_of_lookup_order(self) -> None:
        a = evaluate(
            MatchInput(normalized_upc=UPC, upc_valid_checksum=True), index(by_upc={UPC: (P2, P1)})
        )
        b = evaluate(
            MatchInput(normalized_upc=UPC, upc_valid_checksum=True), index(by_upc={UPC: (P1, P2)})
        )
        assert a.candidates == b.candidates == (P1, P2)
