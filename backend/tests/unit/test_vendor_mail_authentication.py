"""Sender authentication for vendor inventory email (ADR 0041)."""

from __future__ import annotations

import pytest

from app.integrations.vendor_mail.authentication import sender_authenticated

GMAIL_PASS = (
    "mx.google.com; dkim=pass header.i=@haz.example header.s=s1 header.b=abc; "
    "spf=pass (google.com: domain of x@haz.example designates 192.0.2.1 as permitted "
    "sender) smtp.mailfrom=x@haz.example; "
    "dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=haz.example"
)


@pytest.mark.parametrize(
    ("sender", "results"),
    [
        ("stock@haz.example", [GMAIL_PASS]),
        ("Stock@HAZ.Example", [GMAIL_PASS]),  # case does not matter
        ("stock@haz.example", ["mx.google.com; dmarc=pass header.from=haz.example"]),
        # DKIM alone, aligned: signed by the From domain or a parent of it.
        ("stock@haz.example", ["mx.google.com; dkim=pass header.d=haz.example"]),
        ("stock@mail.haz.example", ["mx.google.com; dkim=pass header.i=@haz.example"]),
        # A comment containing ';' does not split the result.
        (
            "stock@haz.example",
            ["mx.google.com; dkim=pass (2048-bit key; unprotected) header.d=haz.example"],
        ),
    ],
)
def test_a_verified_from_domain_is_accepted(sender: str, results: list[str]) -> None:
    assert sender_authenticated(sender, results)


@pytest.mark.parametrize(
    ("sender", "results"),
    [
        ("stock@haz.example", []),
        ("", [GMAIL_PASS]),
        ("not-an-address", [GMAIL_PASS]),
        # SPF authenticates the envelope sender, not the From the routing reads.
        ("stock@haz.example", ["mx.google.com; spf=pass smtp.mailfrom=haz.example"]),
        ("stock@haz.example", ["mx.google.com; dmarc=fail header.from=haz.example"]),
        ("stock@haz.example", ["mx.google.com; dmarc=pass header.from=other.example"]),
        ("stock@haz.example", ["mx.google.com; dkim=pass header.d=evil.example"]),
        # Look-alike suffixes are not parents.
        ("stock@haz.example", ["mx.google.com; dkim=pass header.d=az.example"]),
        # A child domain's signature does not vouch for its parent.
        ("stock@haz.example", ["mx.google.com; dkim=pass header.d=mail.haz.example"]),
        # A bare top-level domain never aligns.
        ("stock@haz.example", ["mx.google.com; dkim=pass header.d=example"]),
        ("stock@haz.example", ["mx.google.com; dkim=neutral header.d=haz.example"]),
        # Only the trusted receiver's verdict counts, and only the topmost one.
        ("stock@haz.example", ["evil.example; dmarc=pass header.from=haz.example"]),
        (
            "stock@haz.example",
            ["mx.google.com; dmarc=fail", "mx.google.com; dmarc=pass header.from=haz.example"],
        ),
    ],
)
def test_an_unverified_from_domain_is_refused(sender: str, results: list[str]) -> None:
    assert not sender_authenticated(sender, results)
