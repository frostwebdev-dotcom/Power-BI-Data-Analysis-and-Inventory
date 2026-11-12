"""Validate a financial CSV locally; no network, database, credentials or file writes."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from app.schemas.profitability import ProfitabilityCsvProfile
from app.services.profitability import (
    MAX_CSV_BYTES,
    ProfitabilityPreviewError,
    preview_profitability_csv,
)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        if key in values:
            raise ProfitabilityPreviewError("DUPLICATE_PROFILE_JSON_KEY")
        values[key] = value
    return values


def main(
    argv: Sequence[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> int:
    target = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    parser = argparse.ArgumentParser(
        description="Preview explicitly mapped financial CSV period totals."
    )
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.profile.open("rb") as source:
            raw_profile = source.read(65_537)
        if len(raw_profile) > 65_536:
            raise ProfitabilityPreviewError("PROFILE_SIZE_LIMIT_EXCEEDED")
        profile = ProfitabilityCsvProfile.model_validate(
            json.loads(raw_profile, object_pairs_hook=_unique_object)
        )
        with args.csv.open("rb") as source:
            content = source.read(MAX_CSV_BYTES + 1)
        preview = preview_profitability_csv(content, profile)
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
        print(
            "Invalid financial profile; check explicit mappings and reporting semantics.",
            file=errors,
        )
        return 2
    except ProfitabilityPreviewError as exc:
        print(f"Financial preview stopped: {exc}", file=errors)
        return 2
    except OSError:
        print("Financial preview input file could not be read.", file=errors)
        return 2
    print(preview.model_dump_json(), file=target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
