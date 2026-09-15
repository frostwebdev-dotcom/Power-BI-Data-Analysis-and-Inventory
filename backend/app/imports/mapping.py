"""The preview the validate endpoint returns: a parsed sample run through
the same :mod:`app.imports.extract` pipeline the import uses.

Nothing here touches the database. Given headers, rows and a
:class:`ProfileRules`, it plans the columns, checks the header signature
(AC-6.4), and extracts every row with its issues.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.imports.extract import ColumnPlan, ExtractedRow, extract_row, plan_columns
from app.imports.profile_rules import ProfileRules, compute_header_signature


@dataclass(slots=True)
class MappingPreview:
    headers: list[str]
    header_signature: str
    expected_signature: str | None
    signature_matches: bool | None
    plan: ColumnPlan
    rows: list[ExtractedRow]
    #: File-level problems: missing columns, a signature mismatch.
    issues: list[str]
    #: Rows are still extracted when a required column is missing, so the
    #: operator sees what *would* happen; the import itself refuses.
    header_ok: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "headers": self.headers,
            "header_signature": self.header_signature,
            "expected_signature": self.expected_signature,
            "signature_matches": self.signature_matches,
            "header_ok": self.header_ok,
            "columns": [
                {
                    "target": c.target,
                    "source": c.source,
                    "index": c.index,
                    "header": c.header,
                    "required": c.required,
                }
                for c in self.plan.columns
            ],
            "issues": self.issues,
            "rows": [
                {
                    "row_number": r.row_number,
                    "status": r.status.value,
                    "raw": r.raw,
                    "values": r.normalized_json(),
                    "issues": [i.as_json() for i in r.issues],
                }
                for r in self.rows
            ],
        }


def header_issues(
    plan: ColumnPlan, headers: list[str], expected_signature: str | None
) -> tuple[str, bool | None, list[str]]:
    """The file-level checks shared by the preview and the import runner."""
    signature = compute_header_signature(headers)
    matches = None if expected_signature is None else signature == expected_signature
    issues = [
        f"required column for {c.target} not found: {c.source!r}" for c in plan.missing_required
    ] + [f"optional column for {c.target} not found: {c.source!r}" for c in plan.missing_optional]
    if matches is False:
        issues.append(
            "header signature does not match the profile; the file's columns differ "
            "from the ones the profile was built for"
        )
    return signature, matches, issues


def map_table(
    rules: ProfileRules,
    headers: list[str],
    rows: list[list[str]],
    *,
    expected_signature: str | None = None,
    first_row_number: int = 1,
) -> MappingPreview:
    plan = plan_columns(rules, headers)
    signature, matches, issues = header_issues(plan, headers, expected_signature)
    if plan.status_index is None and rules.availability_rules.available_when == "status_column":
        issues.append(
            f"status column not found: {rules.availability_rules.status_column!r}; "
            "availability will be UNKNOWN for every row"
        )
    extracted = [
        extract_row(rules, plan, number, cells)
        for number, cells in enumerate(rows, start=first_row_number)
    ]
    return MappingPreview(
        headers=headers,
        header_signature=signature,
        expected_signature=expected_signature,
        signature_matches=matches,
        plan=plan,
        rows=extracted,
        issues=issues,
        header_ok=plan.header_ok,
    )
