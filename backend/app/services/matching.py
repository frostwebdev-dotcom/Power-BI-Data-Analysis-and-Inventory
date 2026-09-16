"""The matching stage of an import (phase 7, AC-7, AC-8 prerequisites).

``match_import_job`` takes a RUNNING job at stage MATCHING, builds the
engine's lookups once for the organization and the job's vendor, runs
:func:`app.matching.engine.evaluate` over every OK and WARNING row, and
records what the chain decided:

* **MATCHED** — ``match_result`` / ``matched_by`` / ``match_priority`` /
  ``product_id`` on the row (the deciding-rule CHECK). The vendor line
  (``vendor_products``) is created or refreshed. An APPROVED mapping is
  never touched by this code (CLAUDE.md §5.2). A match by UPC or catalog
  number gives the line a **PENDING** mapping pointing at the product and
  opens a ``SUGGESTION_ONLY`` queue item: the vendor-SKU mapping becomes
  permanent only when a person approves it. If the chain's product differs
  from an already-approved mapping, the row keeps the chain's answer and a
  ``CONFLICTING_IDENTIFIER`` item asks a person to reconcile.
* **AMBIGUOUS / UNMATCHED / SUGGESTION_ONLY** — ``match_result`` on the
  row, no product, and one queue item per vendor line with the reason, the
  candidates and the full rule trail. A line that already has an open
  item is refreshed rather than duplicated.

Rows superseded by a later duplicate vendor SKU are not matched: the last
occurrence carries the line. The whole trail is also stored on the row
(``normalized_data.match``) without timestamps, so two runs against the
same mapping state are comparable byte for byte (AC-7.5).

The job stays RUNNING; ``current_stage`` moves to SNAPSHOTTING, the step
that follows (phase 9).
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.security import Principal
from app.db.transaction import transaction
from app.matching.engine import MatchIndex, MatchInput, MatchOutcome, evaluate
from app.models.enums import (
    ActorType,
    ExceptionReason,
    ExceptionStatus,
    ImportJobStage,
    ImportJobStatus,
    ImportRowStatus,
    MappingStatus,
    MatchMethod,
    MatchResult,
)
from app.models.ingestion import ImportJob, ImportJobRow
from app.models.matching import ProductMappingException
from app.models.vendor import VendorProduct
from app.repositories.imports import ImportRepository
from app.repositories.matching import MatchIndexRepository
from app.services import audit

_logger = get_logger(__name__)

CHUNK_ROWS: Final = 1000

_REASON_OF: Final = {
    MatchResult.AMBIGUOUS: ExceptionReason.AMBIGUOUS_MATCH,
    MatchResult.UNMATCHED: ExceptionReason.NO_MATCH,
    MatchResult.SUGGESTION_ONLY: ExceptionReason.SUGGESTION_ONLY,
}


class ImportJobNotAtMatching(ConflictError):  # noqa: N818
    code = "import_job_not_at_matching"
    message = "Only a RUNNING import at stage MATCHING can be matched."


def match_import_job(
    session: Session,
    *,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    actor: Principal | None = None,
) -> ImportJob:
    repository = ImportRepository(session, organization_id)
    job = repository.get_job(job_id)
    if job is None:
        from app.services.imports import ImportJobNotFound

        raise ImportJobNotFound()
    if (
        job.status is not ImportJobStatus.RUNNING
        or job.current_stage is not ImportJobStage.MATCHING
    ):
        raise ImportJobNotAtMatching(
            details={
                "status": job.status.value,
                "current_stage": job.current_stage.value if job.current_stage else None,
            }
        )
    _Matcher(session, organization_id, job, actor).run()
    return job


class _Matcher:
    def __init__(
        self, session: Session, organization_id: uuid.UUID, job: ImportJob, actor: Principal | None
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.job = job
        self.actor = actor
        self.repository = MatchIndexRepository(session, organization_id)
        self.index: MatchIndex = self.repository.build(job.vendor_id)
        self.lines: dict[str, VendorProduct] = {}
        self.open_items: dict[uuid.UUID, ProductMappingException] = {}
        self.results: Counter[str] = Counter()
        self.exceptions_opened = 0
        self.exceptions_refreshed = 0
        self.mapping_suggestions = 0
        self.conflicts = 0
        self.superseded = 0
        self.chunks = 0

    # --- the stage ---------------------------------------------------------------------

    def run(self) -> None:
        self._load_vendor_lines()
        self._load_open_items()
        last_row_number = -1
        while True:
            rows = self._next_rows(last_row_number)
            if not rows:
                break
            with transaction(self.session):
                for row in rows:
                    self._match_row(row)
                self.job.matched_rows = self.results[MatchResult.MATCHED.value]
                self.job.exception_rows = (
                    self.results[MatchResult.AMBIGUOUS.value]
                    + self.results[MatchResult.UNMATCHED.value]
                    + self.results[MatchResult.SUGGESTION_ONLY.value]
                )
            self.chunks += 1
            last_row_number = rows[-1].row_number
        self._finish()

    def _next_rows(self, after: int) -> list[ImportJobRow]:
        return list(
            self.session.execute(
                self.repository.select(ImportJobRow)
                .where(
                    ImportJobRow.import_job_id == self.job.id,
                    ImportJobRow.status.in_((ImportRowStatus.OK, ImportRowStatus.WARNING)),
                    ImportJobRow.row_number > after,
                )
                .order_by(ImportJobRow.row_number)
                .limit(CHUNK_ROWS)
            )
            .scalars()
            .all()
        )

    def _load_vendor_lines(self) -> None:
        """Every line of this vendor, keyed by normalised SKU. An APPROVED row
        wins over any other spelling of the same SKU."""
        for line in self.session.execute(
            self.repository.select(VendorProduct)
            .where(VendorProduct.vendor_id == self.job.vendor_id)
            .order_by(VendorProduct.created_at, VendorProduct.id)
        ).scalars():
            current = self.lines.get(line.normalized_vendor_sku)
            if current is None or (
                current.mapping_status is not MappingStatus.APPROVED
                and line.mapping_status is MappingStatus.APPROVED
            ):
                self.lines[line.normalized_vendor_sku] = line

    def _load_open_items(self) -> None:
        for item in self.session.execute(
            self.repository.select(ProductMappingException).where(
                ProductMappingException.vendor_id == self.job.vendor_id,
                ProductMappingException.status == ExceptionStatus.PENDING,
                ProductMappingException.vendor_product_id.is_not(None),
            )
        ).scalars():
            assert item.vendor_product_id is not None
            self.open_items[item.vendor_product_id] = item

    # --- one row -----------------------------------------------------------------------

    def _match_row(self, row: ImportJobRow) -> None:
        data = dict(row.normalized_data)
        if data.get("superseded_by_row") is not None:
            self.superseded += 1
            return

        outcome = evaluate(self._input_of(row), self.index)
        self.results[outcome.result.value] += 1
        row.match_result = outcome.result
        row.matched_by = outcome.method
        row.match_priority = outcome.priority
        row.product_id = outcome.product_id
        data["match"] = outcome.as_json()
        row.normalized_data = data

        line = self._vendor_line(row) if row.vendor_sku else None
        if line is not None:
            row.vendor_product_id = line.id

        if outcome.result is MatchResult.MATCHED:
            assert outcome.product_id is not None and outcome.method is not None
            self._record_match(row, line, outcome)
        else:
            self._open_item(row, line, outcome, _REASON_OF[outcome.result])

    def _input_of(self, row: ImportJobRow) -> MatchInput:
        data = row.normalized_data
        return MatchInput(
            normalized_upc=row.normalized_upc,
            upc_valid_checksum=data.get("upc_valid_checksum"),
            catalog_item_number=row.catalog_item_number,
            vendor_id=self.job.vendor_id,
            normalized_vendor_sku=row.normalized_vendor_sku,
            amazon_seller_sku=None,
            description=row.description,
        )

    def _vendor_line(self, row: ImportJobRow) -> VendorProduct:
        assert row.vendor_sku is not None and row.normalized_vendor_sku is not None
        line = self.lines.get(row.normalized_vendor_sku)
        now = datetime.now(UTC)
        if line is None:
            line = VendorProduct(
                organization_id=self.organization_id,
                vendor_id=self.job.vendor_id,
                vendor_sku=row.vendor_sku,
                normalized_vendor_sku=row.normalized_vendor_sku,
                vendor_description=row.description,
                raw_upc=row.raw_upc,
                normalized_upc=row.normalized_upc,
                pack_size=row.normalized_data.get("pack_size"),
                unit_of_measure=row.normalized_data.get("unit_of_measure"),
                mapping_status=MappingStatus.UNMAPPED,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(line)
            self.session.flush()
            self.lines[row.normalized_vendor_sku] = line
            return line
        line.last_seen_at = now
        if row.description and not line.vendor_description:
            line.vendor_description = row.description
        if row.normalized_upc and not line.normalized_upc:
            line.raw_upc, line.normalized_upc = row.raw_upc, row.normalized_upc
        return line

    def _record_match(
        self, row: ImportJobRow, line: VendorProduct | None, outcome: MatchOutcome
    ) -> None:
        if line is None:
            return  # a UPC-only file: the row is matched, there is no vendor line to map
        if line.mapping_status is MappingStatus.APPROVED:
            if line.product_id != outcome.product_id:
                # The chain (a stronger rule) disagrees with the approved mapping.
                # The mapping is not touched; a person reconciles.
                self.conflicts += 1
                self._open_item(row, line, outcome, ExceptionReason.CONFLICTING_IDENTIFIER)
            return
        if outcome.method in (MatchMethod.UPC, MatchMethod.CATALOG_ITEM_NUMBER):
            # Identity is settled by the identifier; the *vendor-SKU mapping* is
            # a suggestion until approved (CLAUDE.md §5.1 row 3 reads approvals).
            line.product_id = outcome.product_id
            line.mapping_status = MappingStatus.PENDING
            line.mapping_method = outcome.method
            self.mapping_suggestions += 1
            self._open_item(row, line, outcome, ExceptionReason.SUGGESTION_ONLY)

    def _open_item(
        self,
        row: ImportJobRow,
        line: VendorProduct | None,
        outcome: MatchOutcome,
        reason: ExceptionReason,
    ) -> None:
        suggested = outcome.product_id
        score: Decimal | None = None
        if outcome.suggestions:
            suggested = outcome.suggestions[0].product_id
            score = Decimal(str(round(outcome.suggestions[0].score, 4)))
        evaluations: dict[str, Any] = {
            "source": "vendor_import",
            "rules": outcome.evaluations_json(),
        }
        existing = self.open_items.get(line.id) if line is not None else None
        if existing is not None:
            # One open item per vendor line: refresh it with this import's evidence.
            existing.import_job_id = self.job.id
            existing.import_job_row_id = row.id
            existing.reason = reason
            existing.suggested_product_id = suggested
            existing.suggestion_score = score
            existing.candidates = outcome.candidates_json()
            existing.match_evaluations = evaluations
            self.exceptions_refreshed += 1
            return
        item = ProductMappingException(
            organization_id=self.organization_id,
            import_job_id=self.job.id,
            import_job_row_id=row.id,
            vendor_id=self.job.vendor_id,
            vendor_product_id=line.id if line is not None else None,
            reason=reason,
            status=ExceptionStatus.PENDING,
            suggested_product_id=suggested,
            suggestion_score=score,
            candidates=outcome.candidates_json(),
            match_evaluations=evaluations,
        )
        self.session.add(item)
        self.session.flush()
        if line is not None:
            self.open_items[line.id] = item
        self.exceptions_opened += 1

    # --- lifecycle ---------------------------------------------------------------------

    def _finish(self) -> None:
        job = self.job
        before = audit.snapshot(job)
        summary = {
            "by_result": {result.value: self.results[result.value] for result in MatchResult},
            "superseded_skipped": self.superseded,
            "exceptions_opened": self.exceptions_opened,
            "exceptions_refreshed": self.exceptions_refreshed,
            "mapping_suggestions": self.mapping_suggestions,
            "conflicts": self.conflicts,
            "index": {
                "upcs": len(self.index.by_upc),
                "catalog_item_numbers": len(self.index.by_catalog_item_number),
                "approved_vendor_skus": len(self.index.by_vendor_sku),
                "approved_amazon_skus": len(self.index.by_amazon_sku),
            },
            "chunks": self.chunks,
        }
        with transaction(self.session):
            job.current_stage = ImportJobStage.SNAPSHOTTING
            job.error_details = {**job.error_details, "matching": summary}
            audit.record_change(
                self.session,
                organization_id=self.organization_id,
                action="import_job.matched",
                instance=job,
                before=before,
                actor=self.actor,
                actor_type=None if self.actor else ActorType.WORKER,
                actor_label=None if self.actor else "import-runner",
                summary=(
                    f"Matched {job.matched_rows} rows; {job.exception_rows} to the exception "
                    f"queue ({self.exceptions_opened} new items, {self.mapping_suggestions} "
                    "mappings awaiting approval). Waiting for snapshotting."
                ),
            )
        _logger.info(
            "import.matched",
            job_id=str(job.id),
            matched=job.matched_rows,
            exceptions=job.exception_rows,
            conflicts=self.conflicts,
        )
