"""Conditions in explicitly mapped unnamed columns remain separate offers."""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest

from app.imports.profile_rules import compute_header_signature
from app.models.enums import FileFormat
from app.schemas.import_profiles import ImportProfileCreate
from app.services.import_profiles import preview_file, read_options_of

HEADERS = ["UPC", "Description", "Qty", "Cost", ""]


@pytest.mark.parametrize("file_format", [FileFormat.CSV, FileFormat.XLSX])
@pytest.mark.parametrize("old_pin", [False, True])
def test_profile_preview_preserves_conditions_and_requires_reviewed_header_pin(
    file_format: FileFormat, old_pin: bool
) -> None:
    rows: list[list[Any]] = [
        ["012345678905", "Synthetic item", 7, "3.50", "CLEAN"],
        ["012345678905", "Synthetic item", 11, "2.50", "TESTER"],
    ]
    if file_format is FileFormat.CSV:
        output = io.StringIO()
        csv.writer(output).writerows([HEADERS, *rows])
        content = output.getvalue().encode()
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.append(HEADERS)
        for row in rows:
            sheet.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        workbook.close()
        content = buffer.getvalue()
    draft = ImportProfileCreate(
        name="Synthetic conditions",
        file_format=file_format,
        column_map={
            "columns": [
                {"target": target, "source": index}
                for index, target in enumerate(
                    ["upc", "description", "quantity_available", "unit_cost", "offer_variant"]
                )
            ]
        },
        normalization_rules={"vendor_sku_from_offer": True, "review_shared_upc_offers": True},
        header_signature=compute_header_signature(HEADERS[:-1] if old_pin else HEADERS),
    )
    preview, _ = preview_file(
        draft.rules(), read_options_of(draft), content, expected_signature=draft.header_signature
    )
    assert preview.headers == HEADERS
    assert preview.signature_matches is not old_pin
    assert [row.offer_variant for row in preview.rows] == ["CLEAN", "TESTER"]
    assert len({row.vendor_sku for row in preview.rows}) == 2
    assert all(row.upc_shared_by_offers for row in preview.rows)
    assert preview.header_ok
    if old_pin:
        assert len(preview.issues) == 1 and "header signature" in preview.issues[0]
    else:
        assert preview.issues == []
