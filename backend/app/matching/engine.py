"""The deterministic matching engine (CLAUDE.md §5.1, §5.2; AC-7).

Pure: no database, no clock, no randomness. Given a :class:`MatchInput` and
something that answers the four lookups (:class:`MatchLookups` — an
in-memory :class:`MatchIndex` for imports, a SQL-backed catalog for the
Amazon listing sync), :func:`evaluate` returns a :class:`MatchOutcome` that
mirrors ``MatchResult`` exactly and carries every rule tried, in order,
with what it returned. Identical input against identical lookups gives an
identical outcome and an identical attribution — that is the invariant the
tests hold it to.

The rules, in the only order they are ever evaluated:

| Priority | Rule | Reads |
|---|---|---|
| 1 | ``UPC`` | a checksum-valid canonical GTIN against product identifiers |
| 2 | ``CATALOG_ITEM_NUMBER`` | the Nineyard Catalog Item Number |
| 3 | ``VENDOR_SKU_MAPPING`` | an **approved** vendor-SKU mapping for this vendor |
| 4 | ``AMAZON_SKU_MAPPING`` | an **approved** Amazon-SKU mapping |
| 5 | suggestion | product-name similarity — candidates with scores, never a match |

Evaluation stops at the first rule that yields exactly one product. A rule
that yields more than one ends evaluation as ``AMBIGUOUS`` at that
priority; the weaker rules are recorded as skipped and never consulted,
because a weaker rule cannot settle what a stronger one found ambiguous.
Rule 5 runs only when rules 1-4 all yield nothing, and can only ever
produce ``SUGGESTION_ONLY``.

The listing sync (:mod:`app.services.amazon_listings`) uses the same rule
functions with its own, documented policy on top; the rule implementations
live here so there is exactly one of each.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

from app.models.enums import MatchMethod, MatchResult

RuleOutcome = Literal["matched", "ambiguous", "no_match", "skipped", "suggested"]

#: pg_trgm's default similarity threshold; below it a name is not a candidate.
SUGGESTION_THRESHOLD: Final = 0.3
SUGGESTION_LIMIT: Final = 5

PRIORITY_OF: Final[dict[MatchMethod, int]] = {
    MatchMethod.UPC: 1,
    MatchMethod.CATALOG_ITEM_NUMBER: 2,
    MatchMethod.VENDOR_SKU_MAPPING: 3,
    MatchMethod.AMAZON_SKU_MAPPING: 4,
}
SUGGESTION_PRIORITY: Final = 5
SUGGESTION_RULE: Final = "DESCRIPTION_SUGGESTION"


# --- inputs and lookups ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchInput:
    """Everything a row can offer the chain. Any field may be absent."""

    normalized_upc: str | None = None
    #: Only a checksum-valid UPC may drive priority 1 (AC-7.6).
    upc_valid_checksum: bool | None = None
    catalog_item_number: str | None = None
    vendor_id: uuid.UUID | None = None
    normalized_vendor_sku: str | None = None
    amazon_seller_sku: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Suggestion:
    product_id: uuid.UUID
    score: float
    reason: str = "name_similarity"


class GtinLookup(Protocol):
    def products_by_gtin(self, canonical: str) -> list[uuid.UUID]: ...


class AmazonSkuLookup(Protocol):
    def products_by_amazon_sku(self, seller_sku: str) -> list[uuid.UUID]: ...


class MatchLookups(GtinLookup, AmazonSkuLookup, Protocol):
    """What :func:`evaluate` reads. Distinct product ids, sorted, never rows."""

    def products_by_catalog_item_number(self, catalog_item_number: str) -> list[uuid.UUID]: ...

    def products_by_vendor_sku(
        self, vendor_id: uuid.UUID, normalized_vendor_sku: str
    ) -> list[uuid.UUID]: ...

    def suggest_by_description(self, description: str) -> list[Suggestion]: ...


Suggester = Callable[[str], list[Suggestion]]


@dataclass(slots=True)
class MatchIndex:
    """In-memory lookups for one organization (and one vendor), built once
    per import by the repository so 20,000 rows cost no queries."""

    by_upc: dict[str, tuple[uuid.UUID, ...]] = field(default_factory=dict)
    by_catalog_item_number: dict[str, tuple[uuid.UUID, ...]] = field(default_factory=dict)
    #: ``(vendor_id, normalized_vendor_sku)`` → products, APPROVED mappings only.
    by_vendor_sku: dict[tuple[uuid.UUID, str], tuple[uuid.UUID, ...]] = field(default_factory=dict)
    #: seller SKU → products, APPROVED listings only.
    by_amazon_sku: dict[str, tuple[uuid.UUID, ...]] = field(default_factory=dict)
    #: Rule 5's oracle; absent means "no suggestions".
    suggester: Suggester | None = None

    def products_by_gtin(self, canonical: str) -> list[uuid.UUID]:
        return list(self.by_upc.get(canonical, ()))

    def products_by_catalog_item_number(self, catalog_item_number: str) -> list[uuid.UUID]:
        return list(self.by_catalog_item_number.get(catalog_item_number, ()))

    def products_by_vendor_sku(
        self, vendor_id: uuid.UUID, normalized_vendor_sku: str
    ) -> list[uuid.UUID]:
        return list(self.by_vendor_sku.get((vendor_id, normalized_vendor_sku), ()))

    def products_by_amazon_sku(self, seller_sku: str) -> list[uuid.UUID]:
        return list(self.by_amazon_sku.get(seller_sku, ()))

    def suggest_by_description(self, description: str) -> list[Suggestion]:
        return self.suggester(description) if self.suggester else []


def normalize_catalog_item_number(value: str) -> str:
    """Exact match after trimming and case-folding; nothing looser (priority 2)."""
    return " ".join(value.split()).upper()


# --- evaluations and outcomes -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """One rule tried: what went in, what came out. The audit trail."""

    rule: str
    priority: int
    input: str | None
    outcome: RuleOutcome
    candidates: tuple[uuid.UUID, ...] = ()
    note: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "priority": self.priority,
            "input": self.input,
            "outcome": self.outcome,
            "candidates": [str(c) for c in self.candidates],
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """Mirrors ``MatchResult``: MATCHED carries the product, the deciding rule
    and its priority; AMBIGUOUS the priority and the candidates; UNMATCHED
    only the trail; SUGGESTION_ONLY scored candidates."""

    result: MatchResult
    evaluations: tuple[RuleEvaluation, ...]
    product_id: uuid.UUID | None = None
    method: MatchMethod | None = None
    priority: int | None = None
    candidates: tuple[uuid.UUID, ...] = ()
    suggestions: tuple[Suggestion, ...] = ()

    def evaluations_json(self) -> list[dict[str, Any]]:
        return [e.as_json() for e in self.evaluations]

    def candidates_json(self) -> dict[str, Any]:
        """The queue's ``candidates`` column: who was found, by which rule."""
        products: list[dict[str, Any]] = []
        for evaluation in self.evaluations:
            if evaluation.rule == SUGGESTION_RULE:
                continue  # scored below, not repeated as a bare candidate
            for candidate in evaluation.candidates:
                products.append(
                    {
                        "product_id": str(candidate),
                        "rule": evaluation.rule,
                        "priority": evaluation.priority,
                        "identifier": evaluation.input,
                    }
                )
        for suggestion in self.suggestions:
            products.append(
                {
                    "product_id": str(suggestion.product_id),
                    "rule": SUGGESTION_RULE,
                    "priority": SUGGESTION_PRIORITY,
                    "score": round(suggestion.score, 4),
                    "reason": suggestion.reason,
                }
            )
        return {"products": products}

    def as_json(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "product_id": str(self.product_id) if self.product_id else None,
            "method": self.method.value if self.method else None,
            "priority": self.priority,
            "candidates": [str(c) for c in self.candidates],
            "evaluations": self.evaluations_json(),
            "suggestions": [
                {"product_id": str(s.product_id), "score": round(s.score, 4), "reason": s.reason}
                for s in self.suggestions
            ],
        }


# --- the rules ---------------------------------------------------------------------------


def rule_upc(candidate: MatchInput, lookups: GtinLookup) -> RuleEvaluation:
    rule, priority = MatchMethod.UPC.value, PRIORITY_OF[MatchMethod.UPC]
    upc = candidate.normalized_upc
    if not upc:
        return RuleEvaluation(rule, priority, None, "skipped", note="no UPC")
    if candidate.upc_valid_checksum is not True:
        return RuleEvaluation(
            rule, priority, upc, "skipped", note="check digit does not verify; not an identifier"
        )
    hits = _distinct(lookups.products_by_gtin(upc))
    return RuleEvaluation(rule, priority, upc, _outcome(hits), hits)


def rule_catalog_item_number(candidate: MatchInput, lookups: MatchLookups) -> RuleEvaluation:
    rule = MatchMethod.CATALOG_ITEM_NUMBER.value
    priority = PRIORITY_OF[MatchMethod.CATALOG_ITEM_NUMBER]
    if not candidate.catalog_item_number or not candidate.catalog_item_number.strip():
        return RuleEvaluation(rule, priority, None, "skipped", note="no catalog item number")
    number = normalize_catalog_item_number(candidate.catalog_item_number)
    hits = _distinct(lookups.products_by_catalog_item_number(number))
    return RuleEvaluation(rule, priority, number, _outcome(hits), hits)


def rule_vendor_sku_mapping(candidate: MatchInput, lookups: MatchLookups) -> RuleEvaluation:
    rule = MatchMethod.VENDOR_SKU_MAPPING.value
    priority = PRIORITY_OF[MatchMethod.VENDOR_SKU_MAPPING]
    if candidate.vendor_id is None or not candidate.normalized_vendor_sku:
        return RuleEvaluation(rule, priority, None, "skipped", note="no vendor SKU")
    hits = _distinct(
        lookups.products_by_vendor_sku(candidate.vendor_id, candidate.normalized_vendor_sku)
    )
    return RuleEvaluation(rule, priority, candidate.normalized_vendor_sku, _outcome(hits), hits)


def rule_amazon_sku_mapping(candidate: MatchInput, lookups: AmazonSkuLookup) -> RuleEvaluation:
    rule = MatchMethod.AMAZON_SKU_MAPPING.value
    priority = PRIORITY_OF[MatchMethod.AMAZON_SKU_MAPPING]
    sku = (candidate.amazon_seller_sku or "").strip()
    if not sku:
        return RuleEvaluation(rule, priority, None, "skipped", note="no Amazon seller SKU")
    hits = _distinct(lookups.products_by_amazon_sku(sku))
    return RuleEvaluation(rule, priority, sku, _outcome(hits), hits)


_RULES: Final[
    tuple[tuple[MatchMethod, Callable[[MatchInput, MatchLookups], RuleEvaluation]], ...]
] = (
    (MatchMethod.UPC, rule_upc),
    (MatchMethod.CATALOG_ITEM_NUMBER, rule_catalog_item_number),
    (MatchMethod.VENDOR_SKU_MAPPING, rule_vendor_sku_mapping),
    (MatchMethod.AMAZON_SKU_MAPPING, rule_amazon_sku_mapping),
)


# --- the chain ---------------------------------------------------------------------------


def evaluate(candidate: MatchInput, lookups: MatchLookups) -> MatchOutcome:
    """Run the priority chain for one candidate. Reads the lookups; writes nothing."""
    evaluations: list[RuleEvaluation] = []
    for index, (method, rule) in enumerate(_RULES):
        evaluation = rule(candidate, lookups)
        evaluations.append(evaluation)
        if evaluation.outcome == "matched":
            [product_id] = evaluation.candidates
            evaluations.extend(_skip_rest(index + 1, "an earlier rule matched"))
            return MatchOutcome(
                MatchResult.MATCHED,
                tuple(evaluations),
                product_id=product_id,
                method=method,
                priority=evaluation.priority,
                candidates=(product_id,),
            )
        if evaluation.outcome == "ambiguous":
            # Stop here. No fall-through, no pick (AC-7.4).
            evaluations.extend(_skip_rest(index + 1, "an earlier rule was ambiguous"))
            return MatchOutcome(
                MatchResult.AMBIGUOUS,
                tuple(evaluations),
                priority=evaluation.priority,
                candidates=evaluation.candidates,
            )

    # Rules 1-4 yielded nothing. Rule 5 may offer candidates — never a match.
    description = (candidate.description or "").strip()
    if not description:
        evaluations.append(
            RuleEvaluation(
                SUGGESTION_RULE, SUGGESTION_PRIORITY, None, "skipped", note="no description"
            )
        )
        return MatchOutcome(MatchResult.UNMATCHED, tuple(evaluations))
    suggestions = tuple(
        sorted(
            (
                s
                for s in lookups.suggest_by_description(description)
                if s.score >= SUGGESTION_THRESHOLD
            ),
            key=lambda s: (-s.score, str(s.product_id)),
        )[:SUGGESTION_LIMIT]
    )
    evaluations.append(
        RuleEvaluation(
            SUGGESTION_RULE,
            SUGGESTION_PRIORITY,
            description,
            "suggested" if suggestions else "no_match",
            tuple(s.product_id for s in suggestions),
            note=(
                "suggestions require human approval; never an automatic match"
                if suggestions
                else "no product name is similar enough"
            ),
        )
    )
    if suggestions:
        return MatchOutcome(
            MatchResult.SUGGESTION_ONLY,
            tuple(evaluations),
            priority=SUGGESTION_PRIORITY,
            candidates=tuple(s.product_id for s in suggestions),
            suggestions=suggestions,
        )
    return MatchOutcome(MatchResult.UNMATCHED, tuple(evaluations))


def _skip_rest(start: int, note: str) -> list[RuleEvaluation]:
    return [
        RuleEvaluation(method.value, PRIORITY_OF[method], None, "skipped", note=note)
        for method, _ in _RULES[start:]
    ]


def _distinct(ids: Sequence[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    return tuple(sorted(set(ids)))


def _outcome(candidates: Sequence[uuid.UUID]) -> RuleOutcome:
    if len(candidates) == 1:
        return "matched"
    return "ambiguous" if candidates else "no_match"
