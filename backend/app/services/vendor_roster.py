"""Preview and atomically create suppliers from an operator's approved CSV roster."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ValidationFailedError
from app.core.security import Principal
from app.db.transaction import transaction
from app.models.vendor import Vendor
from app.repositories.vendors import VendorRepository
from app.schemas.vendors import VendorCreate
from app.services import audit

MAX_ROWS = 500
TERM_COLUMNS = {
    "payment_terms": "payment",
    "freight_terms": "freight",
    "inventory_cadence": "inventory_cadence",
}


class VendorRosterRequest(BaseModel):
    csv_text: str = Field(min_length=1, max_length=1_000_000)
    apply: bool = False


class RosterRow(BaseModel):
    row_number: int
    code: str
    name: str
    action: Literal["create", "existing", "invalid"]
    issues: list[str] = Field(default_factory=list)


class VendorRosterResponse(BaseModel):
    rows: list[RosterRow]
    ready: bool
    applied: bool = False
    created: int = 0
    existing: int = 0


def parse_roster(content: str) -> tuple[list[RosterRow], list[VendorCreate | None]]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), strict=True)
    try:
        headers = [value.strip() for value in reader.fieldnames or []]
        if (
            not {"code", "name"}.issubset(headers)
            or len(headers) != len(set(headers))
            or set(headers) - (VendorCreate.model_fields.keys() | TERM_COLUMNS.keys())
        ):
            raise ValidationFailedError(
                "Use unique CSV headers from the vendor template, including code and name."
            )
        reader.fieldnames = headers
        rows: list[RosterRow] = []
        payloads: list[VendorCreate | None] = []
        codes: set[str] = set()
        names: set[str] = set()
        for raw in reader:
            if not any(value for value in raw.values()):
                continue
            if len(rows) >= MAX_ROWS:
                raise ValidationFailedError(f"Import at most {MAX_ROWS} vendors at a time.")
            row = RosterRow(
                row_number=reader.line_num,
                code=(raw.get("code") or "").strip(),
                name=(raw.get("name") or "").strip(),
                action="create",
            )
            payload = None
            try:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError("The row must have the same number of cells as the header.")
                values: dict[str, Any] = {
                    key: value.strip() for key, value in raw.items() if value and value.strip()
                }
                if "purchasing_terms" in values:
                    values["purchasing_terms"] = json.loads(values["purchasing_terms"])
                    if not isinstance(values["purchasing_terms"], dict):
                        raise ValueError("purchasing_terms must be a JSON object.")
                for column, term in TERM_COLUMNS.items():
                    if column in values:
                        values.setdefault("purchasing_terms", {})[term] = values.pop(column)
                payload = VendorCreate.model_validate(values)
                row.code, row.name = payload.code, payload.name
                name_key = " ".join(payload.name.split()).casefold()
                if payload.code in codes or name_key in names:
                    raise ValueError("Duplicate vendor code or name in this file.")
                codes.add(payload.code)
                names.add(name_key)
            except ValidationError as exc:
                row.issues = [
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                ]
            except ValueError as exc:
                row.issues = [str(exc)]
            if row.issues:
                row.action = "invalid"
                payload = None
            rows.append(row)
            payloads.append(payload)
    except csv.Error as exc:
        raise ValidationFailedError(
            "The CSV file is malformed; check its quotes and columns."
        ) from exc
    if not rows:
        raise ValidationFailedError("The CSV must contain at least one vendor row.")
    return rows, payloads


def setup_roster(
    session: Session, principal: Principal, request: VendorRosterRequest
) -> VendorRosterResponse:
    rows, payloads = parse_roster(request.csv_text)
    repository = VendorRepository(session, principal.organization_id)
    existing = session.scalars(repository.select(Vendor)).all()
    by_code = {vendor.code: vendor for vendor in existing}
    by_name = {" ".join(vendor.name.split()).casefold(): vendor for vendor in existing}
    for row, payload in zip(rows, payloads, strict=True):
        if payload is None:
            continue
        name_key = " ".join(payload.name.split()).casefold()
        vendor = by_code.get(payload.code)
        if vendor is not None and " ".join(vendor.name.split()).casefold() == name_key:
            row.action = "existing"
            row.issues = [
                "Already exists; existing settings are preserved. Edit this vendor to change them."
            ]
        elif vendor is not None or name_key in by_name:
            row.action = "invalid"
            row.issues = [
                "Code or name conflicts with an existing vendor. Use its existing code and name."
            ]
    response = VendorRosterResponse(
        rows=rows,
        ready=all(row.action != "invalid" for row in rows),
        existing=sum(row.action == "existing" for row in rows),
    )
    if not request.apply or not response.ready:
        return response
    try:
        with transaction(session):
            for row, payload in zip(rows, payloads, strict=True):
                if row.action != "create" or payload is None:
                    continue
                vendor = repository.add(Vendor(**payload.model_dump()))
                session.flush()
                audit.record_change(
                    session,
                    organization_id=principal.organization_id,
                    action="vendor.created",
                    instance=vendor,
                    before=None,
                    actor=principal,
                    summary=f"Vendor {vendor.code} created from the approved vendor roster.",
                )
                response.created += 1
    except IntegrityError as exc:
        raise ConflictError(
            "Vendor records changed during setup. Preview the roster again."
        ) from exc
    response.applied = True
    return response
