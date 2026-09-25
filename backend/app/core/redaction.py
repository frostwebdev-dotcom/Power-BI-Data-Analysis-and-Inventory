"""Redaction of secrets in log output and audit payloads.

Credentials reach logs by accident, not by design: a request-header dump, an
exception repr containing a connection string, a third-party library logging a
URL with an embedded password. Redaction has to happen at the sink, because no
amount of care at every call site is reliable.

Two layers, both applied:

* **key-based** — any mapping key that looks sensitive has its value replaced,
  recursively through nested structures;
* **value-based** — strings are scanned for credential *shapes* (a bearer token,
  a JWT, a URL with a password) regardless of what key they arrived under.

The same functions redact audit payloads, so a secret never lands in
``audit_events.before`` / ``after`` either (AC-12.6).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any, Final

REDACTED: Final = "***REDACTED***"

# Guards against a pathological or cyclic structure turning one log line into an
# unbounded traversal.
_MAX_DEPTH: Final = 8

# Substring match, case-insensitive, against the key name. Deliberately broad:
# a false positive costs one unreadable log field, a false negative leaks a
# credential.
_SENSITIVE_KEY_PARTS: Final[tuple[str, ...]] = (
    "authorization",
    "auth_header",
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "x-api-key",
    "access_key",
    "private_key",
    "credential",
    "cookie",
    "session_id",
    "client_secret",
    # Amazon SP-API / Login with Amazon (ADR 0011). "token" and "secret" above
    # already cover refresh_token, client_secret and x-amz-access-token; they
    # are listed by name so the intent survives a future edit of the generic
    # entries. "lwa_" catches the client id too — an identifier rather than a
    # secret, but of no use in a log and it names the application.
    "refresh_token",
    "x-amz-access-token",
    "lwa_",
    "connection_string",
    "dsn",
    # A DSN carries a password. Masked whole under its own key; a DSN appearing
    # inside free text has only its password masked, by _URL_CREDENTIALS_RE.
    "database_url",
    "database_uri",
    "db_url",
    "db_uri",
)

# Keys that merely *contain* a sensitive substring but are safe and useful.
# Without these, "token_type": "bearer" and expiry metadata become unreadable.
_SAFE_KEY_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        "token_type",
        "token_expires_in",
        "expires_in",
        "has_token",
        "token_count",
        # A truncated hash of a token, deliberately logged so two runs can be
        # told apart. It is not a credential and cannot be reversed into one —
        # redacting it would leave the fingerprint with no purpose.
        "token_fingerprint",
    }
)

# `Bearer <credential>` in any header-ish string.
_BEARER_RE: Final = re.compile(r"(?i)\b(bearer|basic|digest)\s+([A-Za-z0-9\-._~+/=]{8,})")

# A JWT: three base64url segments. Matched on its own so a bare token pasted
# into a message is caught even without the `Bearer` prefix.
_JWT_RE: Final = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")

# Credentials embedded in a URL: scheme://user:password@host
_URL_CREDENTIALS_RE: Final = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]+):([^\s@]+)@")

# Sensitive URL query values. This is defense in depth for third-party logs:
# httpx INFO logging is disabled, but a warning or exception may still include
# the request URL. Preserve the key for diagnosis and mask only its value.
_SENSITIVE_QUERY_PARAM_RE: Final = re.compile(
    r"(?i)([?&](?:nexttoken|access_token|refresh_token|x-amz-signature|"
    r"x-amz-credential|x-amz-security-token)=)([^&\s\"']*)"
)

# `api_key=...`, `password: ...`, `token=...` inside an otherwise plain string.
_INLINE_ASSIGNMENT_RE: Final = re.compile(
    r"(?i)\b(api[_-]?key|password|passwd|pwd|secret|access[_-]?token|refresh[_-]?token"
    r"|client[_-]?secret|authorization)\b(\s*[=:]\s*)([^\s,;&\"')]+)"
)

# The same names as JSON object members: `"access_token": "..."`. The closing
# quote after the key defeats _INLINE_ASSIGNMENT_RE, so a raw token-endpoint
# response body logged by an HTTP library would otherwise pass through intact.
_JSON_FIELD_RE: Final = re.compile(
    r'(?i)("(?:access_token|refresh_token|client_secret|id_token|api[_-]?key|password'
    r'|secret|authorization)"\s*:\s*")([^"]*)(")'
)

# Login with Amazon tokens have a fixed prefix: `Atza|` for access tokens,
# `Atzr|` for refresh tokens. Matched bare, so one pasted into any message is
# masked whatever surrounds it.
_LWA_TOKEN_RE: Final = re.compile(r"\bAtz[ar]\|[A-Za-z0-9_\-]{16,}")


def is_sensitive_key(key: str) -> bool:
    """Whether a mapping key's value should be masked outright."""
    lowered = key.strip().lower()
    if lowered in _SAFE_KEY_EXCEPTIONS:
        return False
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def redact_text(value: str) -> str:
    """Mask credential shapes inside a free-text string."""
    masked = _URL_CREDENTIALS_RE.sub(rf"\1:{REDACTED}@", value)
    masked = _SENSITIVE_QUERY_PARAM_RE.sub(rf"\1{REDACTED}", masked)
    masked = _BEARER_RE.sub(rf"\1 {REDACTED}", masked)
    masked = _JWT_RE.sub(REDACTED, masked)
    masked = _LWA_TOKEN_RE.sub(REDACTED, masked)
    masked = _JSON_FIELD_RE.sub(rf"\1{REDACTED}\3", masked)
    return _INLINE_ASSIGNMENT_RE.sub(rf"\1\2{REDACTED}", masked)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact a value of any shape.

    Mappings are walked by key; sequences element-wise; strings scanned for
    credential patterns. Anything else is returned untouched, except that a
    non-string scalar under a sensitive key is masked by ``redact_mapping``.
    """
    if _depth >= _MAX_DEPTH:
        return value

    if isinstance(value, str):
        return redact_text(value)

    if isinstance(value, Mapping):
        return redact_mapping(value, _depth=_depth)

    # str and bytes are Sequences; they are handled above and below.
    if isinstance(value, bytes | bytearray):
        return value

    if isinstance(value, Sequence):
        return [redact(item, _depth=_depth + 1) for item in value]

    if isinstance(value, set | frozenset):
        return {redact(item, _depth=_depth + 1) for item in value}

    return value


def redact_mapping(mapping: Mapping[str, Any], *, _depth: int = 0) -> dict[str, Any]:
    """Redact a mapping, masking values whose key looks sensitive."""
    if _depth >= _MAX_DEPTH:
        return dict(mapping)

    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        key_str = str(key)
        if is_sensitive_key(key_str):
            # Masked whole, whatever the type — a sensitive key never has a
            # value worth partially preserving.
            redacted[key_str] = REDACTED
        else:
            redacted[key_str] = redact(value, _depth=_depth + 1)
    return redacted


def redaction_processor(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> Mapping[str, Any]:
    """structlog processor that redacts every event before it is rendered.

    Placed last in the chain, immediately before the renderer, so it sees the
    fully assembled event including anything merged in from context variables.
    """
    return redact_mapping(event_dict)
