"""Import Nineyard Products CSV. Defaults to a read-only database dry run."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.transaction import session_lifecycle, transaction
from app.imports.storage import build_storage_backend
from app.integrations.nineyard.catalogue_csv import read_catalogue_export
from app.models import Organization
from app.services.nineyard_catalogue_import import apply_catalogue_import, plan_catalogue_import


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--org-slug")
    parser.add_argument("--file-only", action="store_true", help="Validate CSV without a database")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only database preview (default)")
    mode.add_argument("--apply", action="store_true", help="Apply the reviewed CSV atomically")
    parser.add_argument("--expected-sha256", help="Required with --apply; hash from the dry run")
    parser.add_argument("--operator-label", help="Required with --apply; person performing import")
    args = parser.parse_args(argv)
    if args.file_only and args.apply:
        parser.error("--file-only cannot be combined with --apply")
    if not args.file_only and not args.org_slug:
        parser.error("--org-slug is required for database checks")
    if args.apply and (not args.expected_sha256 or not args.operator_label):
        parser.error("--apply requires --expected-sha256 and --operator-label")
    try:
        content = args.file.read_bytes()
        export = read_catalogue_export(content)
        if args.expected_sha256 and export.sha256 != args.expected_sha256.lower():
            raise ValueError("File SHA-256 does not match the reviewed dry run")
        report: dict[str, Any] = {
            "mode": "file-only" if args.file_only else "dry-run",
            "sha256": export.sha256,
            "file": export.counts,
            "issues": list(export.issues[:20]),
            "errors": list(export.errors[:20]),
        }
        errors = bool(export.errors) or not export.rows
        if not args.file_only:
            with session_lifecycle() as session:
                if not args.apply:
                    session.execute(text("SET TRANSACTION READ ONLY"))
                organization_id = session.scalar(
                    select(Organization.id).where(
                        Organization.slug == args.org_slug, Organization.is_active.is_(True)
                    )
                )
                if organization_id is None:
                    raise ValueError("An active organization with that slug is required")
                if args.apply:
                    with transaction(session):
                        applied = apply_catalogue_import(
                            session,
                            organization_id,
                            export,
                            content=content,
                            file_name=args.file.name,
                            storage=build_storage_backend(get_settings()),
                            operator_label=args.operator_label,
                        )
                        report.update(applied)
                else:
                    plan = plan_catalogue_import(session, organization_id, export)
                    report["database"] = plan.counts
                    report["errors"] = plan.errors[:20]
                    errors = errors or bool(plan.errors)
        print(json.dumps(report, indent=2))
        return 1 if errors else 0
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        print(f"Catalogue import refused: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(
            f"Catalogue database operation failed ({type(exc).__name__}). "
            "Check connectivity and apply the required migrations first.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
