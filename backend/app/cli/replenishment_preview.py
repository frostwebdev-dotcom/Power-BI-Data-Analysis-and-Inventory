"""Preview explicit JSON inputs locally; never loads credentials or connects to data."""

from __future__ import annotations

import json
import sys
from typing import TextIO

from pydantic import ValidationError

from app.schemas.replenishment import ReplenishmentPreviewInput
from app.services.replenishment import calculate_replenishment

MAX_INPUT_CHARACTERS = 1_048_576


def main(
    *, stdin: TextIO | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> int:
    source = stdin if stdin is not None else sys.stdin
    target = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    raw = source.read(MAX_INPUT_CHARACTERS + 1)
    if len(raw) > MAX_INPUT_CHARACTERS:
        print("Preview input exceeds the size limit.", file=errors)
        return 2
    try:
        inputs = ReplenishmentPreviewInput.model_validate_json(raw)
    except ValidationError:
        # Do not echo arbitrary input or field values into console/log output.
        print("Invalid preview JSON. Check the documented policy and input fields.", file=errors)
        return 2
    result = calculate_replenishment(inputs)
    payload = {
        "preview_only": True,
        "data_source": "explicit_inputs",
        "request": inputs.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
    }
    print(json.dumps(payload, sort_keys=True), file=target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
