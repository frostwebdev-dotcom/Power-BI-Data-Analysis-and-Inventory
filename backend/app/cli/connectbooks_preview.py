"""Preview a signed ConnectBooks workbook locally without writing or importing it."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from app.cli.profitability_preview import _unique_object
from app.schemas.connectbooks import ConnectBooksXlsxProfile
from app.services.connectbooks import MAX_XLSX_BYTES, preview_connectbooks_xlsx
from app.services.profitability import ProfitabilityPreviewError


def main(
    argv: Sequence[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> int:
    target = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    parser = argparse.ArgumentParser(description="Preview signed ConnectBooks XLSX period totals.")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--xlsx", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.profile.open("rb") as source:
            raw_profile = source.read(65_537)
        if len(raw_profile) > 65_536:
            raise ProfitabilityPreviewError("PROFILE_SIZE_LIMIT_EXCEEDED")
        profile = ConnectBooksXlsxProfile.model_validate(
            json.loads(raw_profile, object_pairs_hook=_unique_object)
        )
        with args.xlsx.open("rb") as source:
            content = source.read(MAX_XLSX_BYTES + 1)
        result = preview_connectbooks_xlsx(content, profile)
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
        print("Invalid ConnectBooks profile; check explicit reporting context.", file=errors)
        return 2
    except ProfitabilityPreviewError as exc:
        print(f"ConnectBooks preview stopped: {exc}", file=errors)
        return 2
    except OSError:
        print("ConnectBooks input file could not be read.", file=errors)
        return 2
    print(result.model_dump_json(), file=target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
