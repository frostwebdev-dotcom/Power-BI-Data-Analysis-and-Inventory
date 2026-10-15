"""Read a Nineyard Products export without changing its bytes or identifiers."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app.matching.normalize import normalize_gtin

REQUIRED_HEADERS = frozenset({"ItemId", "Product ID", "Title", "Upc"})


@dataclass(frozen=True)
class CatalogueBarcode:
    raw: str
    canonical: str


@dataclass(frozen=True)
class CatalogueRow:
    row_number: int
    item_id: str
    catalog_item_number: str
    title: str
    barcodes: tuple[CatalogueBarcode, ...]
    raw: dict[str, str]


@dataclass(frozen=True)
class CatalogueExport:
    rows: tuple[CatalogueRow, ...]
    sha256: str
    counts: dict[str, int]
    issues: tuple[dict[str, Any], ...]
    errors: tuple[dict[str, Any], ...]


def read_catalogue_export(content: bytes) -> CatalogueExport:
    """Resolve named headers, not column positions. Structural errors block apply.

    A cell may contain comma-separated UPC/EAN/GTIN values. Keep leading zeros,
    reject scientific notation and short codes without a supplied check digit,
    and use the same checksum/canonical normalization as the matching engine.
    """
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    headers = next(reader, [])
    if len(set(headers)) != len(headers) or not REQUIRED_HEADERS.issubset(headers):
        raise ValueError("CSV needs unique headers including ItemId, Product ID, Title and Upc")
    rows: list[CatalogueRow] = []
    issues: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    item_ids: set[str] = set()
    business_ids: set[str] = set()
    counts: Counter[str] = Counter()
    owners: defaultdict[str, set[str]] = defaultdict(set)
    for row_number, values in enumerate(reader, 2):
        if not any(value.strip() for value in values):
            continue
        if len(values) != len(headers):
            errors.append({"row": row_number, "code": "COLUMN_COUNT"})
            continue
        raw = dict(zip(headers, values, strict=True))
        item_id = raw["ItemId"].strip()
        business_id = raw["Product ID"].strip()
        if not re.fullmatch(r"[1-9][0-9]*", item_id) or not business_id:
            errors.append({"row": row_number, "code": "INVALID_IDENTITY"})
            continue
        if item_id in item_ids or business_id.casefold() in business_ids:
            errors.append({"row": row_number, "code": "DUPLICATE_IDENTITY"})
        item_ids.add(item_id)
        business_ids.add(business_id.casefold())
        tokens = [value.strip() for value in raw["Upc"].split(",") if value.strip()]
        counts["rows_missing_barcode"] += not tokens
        counts["rows_multiple_barcodes"] += len(tokens) > 1
        barcodes: dict[str, CatalogueBarcode] = {}
        for token in tokens:
            counts["barcode_values"] += 1
            normalized = normalize_gtin(token)
            problem = normalized.problem
            if normalized.digits and set(normalized.digits) == {"0"}:
                problem = "zero placeholder"
            elif len(re.sub(r"[\s._-]", "", token)) < 8:
                problem = "barcode must include a check digit"
            if problem or not normalized.usable or normalized.canonical is None:
                counts["invalid_barcode_values"] += 1
                issues.append(
                    {
                        "row": row_number,
                        "item_id": item_id,
                        "code": "UPC_INVALID",
                        "raw": token,
                        "reason": problem,
                    }
                )
                continue
            counts["valid_barcode_values"] += 1
            barcodes.setdefault(normalized.canonical, CatalogueBarcode(token, normalized.canonical))
            owners[normalized.canonical].add(item_id)
        counts["rows_with_valid_barcode"] += bool(barcodes)
        rows.append(
            CatalogueRow(
                row_number,
                item_id,
                business_id,
                raw["Title"].strip(),
                tuple(barcodes.values()),
                raw,
            )
        )
    counts["products"] = len(rows)
    counts["unique_valid_barcodes"] = len(owners)
    counts["shared_barcodes"] = sum(len(ids) > 1 for ids in owners.values())
    counts["structural_errors"] = len(errors)
    return CatalogueExport(
        tuple(rows), hashlib.sha256(content).hexdigest(), dict(counts), tuple(issues), tuple(errors)
    )
