"""Identifier normalisation for the matching engine (CLAUDE.md §5.1, AC-7.6).

One function, :func:`normalize_gtin`, turns whatever a vendor file, a
marketplace report or a catalog source calls a "UPC" into the canonical
comparison form: **GTIN-14, zero-padded, check digit verified**. Values
that differ only in leading zeros, whitespace or hyphens compare equal;
UPC-E is expanded to UPC-A first; an invalid check digit is reported rather
than silently accepted, because a value with a bad check digit must not
match at priority 1 (AC-7.6).

Nothing here consults the database or knows about products. It is pure, so
the same rules apply wherever an identifier is read, and a test can pin
every case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_NON_DIGIT: Final = re.compile(r"[^0-9]")
#: Characters we strip silently. Anything else that is not a digit makes the
#: value unusable rather than "close enough".
_SEPARATORS: Final = re.compile(r"[\s\-_.]")

#: Recognised GTIN lengths after separator stripping.
UPC_E_LENGTHS: Final = frozenset({6, 7, 8})
GTIN_LENGTHS: Final = frozenset({8, 12, 13, 14})


@dataclass(frozen=True, slots=True)
class NormalizedGtin:
    """The outcome of normalising one identifier value."""

    raw: str
    #: Digits after stripping separators (and UPC-E expansion), or ``""``.
    digits: str
    #: ``UPC-A`` / ``EAN-13`` / ``GTIN-14`` / ``EAN-8`` / ``UPC-E``, or ``None``.
    kind: str | None
    #: The 14-digit comparison form, or ``None`` when the value is unusable.
    canonical: str | None
    #: Whether the check digit verified. ``False`` disqualifies the value from
    #: automatic priority-1 matching; ``None`` when there was nothing to check.
    valid_checksum: bool | None
    #: Why the value is unusable, when it is.
    problem: str | None = None
    #: ``False`` for a six- or seven-digit UPC-E, whose check digit is
    #: *computed* during expansion rather than read from the value. Such a
    #: checksum verifies by construction, so it proves nothing about the
    #: value and cannot support an automatic match.
    check_digit_supplied: bool = True

    @property
    def usable(self) -> bool:
        """Safe to use for an automatic match."""
        return (
            self.canonical is not None and self.valid_checksum is True and self.check_digit_supplied
        )


def gtin_check_digit(digits_without_check: str) -> int:
    """The GTIN modulo-10 check digit for a run of digits.

    Weights alternate 3, 1, 3, 1 … counted from the rightmost digit, which
    is the same rule for every GTIN length once the value is right-aligned.
    """
    total = 0
    for index, char in enumerate(reversed(digits_without_check)):
        weight = 3 if index % 2 == 0 else 1
        total += int(char) * weight
    return (10 - total % 10) % 10


def expand_upc_e(digits: str) -> str | None:
    """Expand a UPC-E value (6, 7 or 8 digits) to the 12-digit UPC-A.

    Accepts the bare six data digits, seven (number system + data), or eight
    (number system + data + check). Only number system 0 is defined for
    UPC-E. Returns ``None`` if the value is not a UPC-E shape.
    """
    if len(digits) == 8:
        system, data, check = digits[0], digits[1:7], digits[7]
    elif len(digits) == 7:
        system, data, check = digits[0], digits[1:7], None
    elif len(digits) == 6:
        system, data, check = "0", digits, None
    else:
        return None
    if system != "0":
        return None

    d1, d2, d3, d4, d5, d6 = tuple(data)
    if d6 in "012":
        body = f"{d1}{d2}{d6}0000{d3}{d4}{d5}"
    elif d6 == "3":
        body = f"{d1}{d2}{d3}00000{d4}{d5}"
    elif d6 == "4":
        body = f"{d1}{d2}{d3}{d4}00000{d5}"
    else:
        body = f"{d1}{d2}{d3}{d4}{d5}0000{d6}"

    upc_a_without_check = system + body
    computed = str(gtin_check_digit(upc_a_without_check))
    # A supplied check digit is kept as supplied so a bad one is *reported*
    # by the caller's validation rather than silently corrected here.
    return upc_a_without_check + (check if check is not None else computed)


def normalize_gtin(raw: str | None) -> NormalizedGtin:
    """Normalise a UPC / EAN / GTIN value to its canonical GTIN-14 form.

    Rules, in order:

    1. strip whitespace, hyphens, underscores and dots;
    2. any other non-digit character makes the value unusable;
    3. 11 digits is a UPC-A that lost its leading zero (a spreadsheet
       formatted it as a number) — the zero is restored;
    4. 6, 7 or 8 digits are tried as UPC-E and expanded; 8 digits that do not
       expand are treated as EAN-8;
    5. the check digit is verified on the resulting 8/12/13/14-digit value;
    6. the canonical form is the value left-padded with zeros to 14 digits.

    A 6- or 7-digit UPC-E carries no check digit, so expansion has to compute
    one. The canonical form is still returned (it identifies the value for
    display, search and derived vendor identities) but the result is not
    :attr:`~NormalizedGtin.usable`: a check digit we computed ourselves cannot
    show that the value was recorded correctly.
    """
    if raw is None:
        return NormalizedGtin("", "", None, None, None, "empty")
    stripped = _SEPARATORS.sub("", raw)
    if not stripped:
        return NormalizedGtin(raw, "", None, None, None, "empty")
    if _NON_DIGIT.search(stripped):
        return NormalizedGtin(raw, "", None, None, None, "contains non-digit characters")

    digits = stripped
    kind: str | None = None
    check_digit_supplied = len(digits) not in (6, 7)

    if len(digits) == 11:
        digits = "0" + digits
        kind = "UPC-A"
    elif len(digits) in UPC_E_LENGTHS:
        expanded = expand_upc_e(digits)
        if expanded is not None:
            digits = expanded
            kind = "UPC-E"
        elif len(digits) == 8:
            kind = "EAN-8"
        else:
            return NormalizedGtin(
                raw,
                digits,
                None,
                None,
                None,
                "not a UPC-E value",
                check_digit_supplied=check_digit_supplied,
            )

    if len(digits) not in GTIN_LENGTHS:
        return NormalizedGtin(raw, digits, None, None, None, f"unsupported length {len(digits)}")

    if kind is None:
        kind = {8: "EAN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}[len(digits)]

    valid = gtin_check_digit(digits[:-1]) == int(digits[-1])
    canonical = digits.zfill(14)
    if not valid:
        problem: str | None = "check digit does not verify"
    elif not check_digit_supplied:
        problem = "no check digit supplied; not usable for automatic matching"
    else:
        problem = None
    return NormalizedGtin(
        raw,
        digits,
        kind,
        canonical,
        valid,
        problem,
        check_digit_supplied=check_digit_supplied,
    )
