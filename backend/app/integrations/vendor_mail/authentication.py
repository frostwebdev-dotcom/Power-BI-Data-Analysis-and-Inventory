"""Was a vendor email really sent by the address in its ``From`` header?

Routing an email to a vendor by its ``From`` address alone lets anyone who can
send mail to the intake mailbox inject a stock list under a vendor's name. The
receiving server records what it verified in an ``Authentication-Results``
header (RFC 8601); this module reads that header and accepts a sender only
when the receiver vouches for the ``From`` domain:

* ``dmarc=pass`` for the ``From`` domain, or
* ``dkim=pass`` with a signing domain aligned (equal, or a parent) with it.

SPF on its own is not enough: it authenticates the envelope sender, which need
not match the ``From`` address the routing rules read.

Only the *topmost* ``Authentication-Results`` header counts, and only from a
trusted receiver (``mx.google.com`` for Gmail). Headers further down were part
of the message as it arrived and may have been written by the sender.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

#: Receivers whose ``Authentication-Results`` this intake trusts.
GMAIL_AUTHSERV_ID: Final = "mx.google.com"

_COMMENT: Final = re.compile(r"\([^()]*\)")


def sender_authenticated(
    sender: str,
    authentication_results: Sequence[str],
    *,
    trusted_authserv_ids: frozenset[str] = frozenset({GMAIL_AUTHSERV_ID}),
) -> bool:
    """True only when the trusted receiver verified ``sender``'s domain."""
    _, at, domain = sender.strip().lower().rpartition("@")
    domain = domain.rstrip(".")
    if not at or not domain or not authentication_results:
        return False
    header = authentication_results[0]
    # Comments may contain ';' or '=' — remove them (they nest rarely; loop).
    previous = None
    while previous != header:
        previous, header = header, _COMMENT.sub(" ", header)
    authserv, _, results = header.partition(";")
    authserv_tokens = authserv.split()
    if not authserv_tokens or authserv_tokens[0].lower() not in trusted_authserv_ids:
        return False
    for resinfo in results.split(";"):
        tokens = resinfo.split()
        if not tokens or "=" not in tokens[0]:
            continue
        method, _, result = tokens[0].lower().partition("=")
        if result != "pass":
            continue
        properties: dict[str, str] = {}
        for token in tokens[1:]:
            key, sep, value = token.partition("=")
            if sep:
                properties[key.lower()] = value.strip('"').lower().rstrip(".")
        if method == "dmarc" and _aligned(properties.get("header.from", ""), domain):
            return True
        if method == "dkim":
            identity = properties.get("header.i", "").rpartition("@")[2]
            if _aligned(properties.get("header.d") or identity, domain):
                return True
    return False


def _aligned(verified_domain: str, sender_domain: str) -> bool:
    """Relaxed alignment: the verified domain is the sender's, or a parent of it."""
    if not verified_domain or "." not in verified_domain:
        return False
    return sender_domain == verified_domain or sender_domain.endswith("." + verified_domain)
