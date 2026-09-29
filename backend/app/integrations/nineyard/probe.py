"""The read-only Nineyard diagnostic.

This describes what the API actually returned. It does not validate against an
expected schema, because there is no confirmed schema yet — that is the thing
being discovered (CLAUDE.md working agreements: plan before code, and do not
invent what has not been observed).

Two habits keep the output honest:

* anything inferred is labelled a **candidate**. The probe does not decide that
  ``totalCount`` is the record total; it reports that a key by that name exists
  and holds an integer.
* nothing is asserted about fields that were absent. A field missing from the
  sample is reported as missing, not as optional — those are different claims,
  and only the first is supported by one run.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from app.core.logging import get_logger
from app.integrations.nineyard.client import NineyardClient
from app.integrations.nineyard.errors import NineyardError
from app.integrations.nineyard.sanitize import (
    DEFAULT_SAMPLE_SIZE,
    field_names,
    field_presence,
    sanitize,
)

_logger = get_logger(__name__)

#: The read-only endpoint groups under investigation. GET only, by construction:
#: the client has no other verb.
READ_ONLY_ENDPOINTS: Final[dict[str, str]] = {
    "Items": "/api/Items",
    "Skus": "/api/Skus",
    "Vendors": "/api/Vendors",
    "PurchaseOrders": "/api/PurchaseOrders",
}

# These parameters are required by the observed Nineyard contract. In
# particular, omitting Page/PerPage from Items returns HTTP 400 and omitting
# PageNumber from Skus returns HTTP 500 for the client account. Keeping the
# defaults here makes the normal probe command useful without requiring users
# to know endpoint-specific pagination details.
READ_ONLY_ENDPOINT_DEFAULT_PARAMS: Final[dict[str, dict[str, Any]]] = {
    "Items": {"Page": 1, "PerPage": DEFAULT_SAMPLE_SIZE},
    "Skus": {"PageNumber": 1},
    "Vendors": {"Page": 1, "PerPage": DEFAULT_SAMPLE_SIZE},
    "PurchaseOrders": {"Page": 1, "PerPage": DEFAULT_SAMPLE_SIZE},
}

#: Key names that *often* carry paging information. Presence is reported; meaning
#: is not assumed. Nineyard may use none of these, or use one of them for
#: something else entirely.
PAGINATION_KEY_CANDIDATES: Final[frozenset[str]] = frozenset(
    {
        "page",
        "pageNumber",
        "pageIndex",
        "pageSize",
        "perPage",
        "limit",
        "offset",
        "skip",
        "take",
        "total",
        "totalCount",
        "totalRecords",
        "totalItems",
        "totalPages",
        "count",
        "recordCount",
        "hasMore",
        "hasNextPage",
        "nextPage",
        "next",
        "previous",
        "links",
    }
)

#: Key names that often hold the record array in an envelope response.
RECORD_CONTAINER_CANDIDATES: Final[tuple[str, ...]] = (
    "data",
    "items",
    "results",
    "records",
    "value",
    "content",
    "rows",
    "list",
)


@dataclass(slots=True)
class EndpointReport:
    """What one endpoint returned. Every field is observed, none inferred."""

    name: str
    path: str
    requested_params: dict[str, Any] = field(default_factory=dict)

    status: int | None = None
    content_type: str | None = None
    elapsed_ms: float | None = None

    #: "object", "array", "null", or the Python type name for anything else.
    top_level_type: str | None = None
    top_level_keys: list[str] = field(default_factory=list)

    #: Which key held the record array, when the response was an envelope.
    record_container_key: str | None = None
    record_count: int | None = None
    record_field_names: list[str] = field(default_factory=list)
    record_field_presence: dict[str, int] = field(default_factory=dict)

    #: Keys whose names resemble paging metadata, with their values. Reported as
    #: candidates; the probe does not claim to know what they mean.
    pagination_candidates: dict[str, Any] = field(default_factory=dict)

    #: Structure and types only — never real values.
    sanitized_sample: Any | None = None

    error_type: str | None = None
    error_message: str | None = None
    error_guidance: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error_type is None and self.status is not None and self.status < 300

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProbeReport:
    """The whole run."""

    base_url: str
    authenticated: bool = False
    token_fingerprint: str | None = None
    token_expires_in: int | None = None
    token_expires: str | None = None
    token_response_keys: list[str] = field(default_factory=list)
    auth_error: str | None = None
    auth_error_guidance: str | None = None
    endpoints: list[EndpointReport] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "authenticated": self.authenticated,
            "token_fingerprint": self.token_fingerprint,
            "token_expires_in": self.token_expires_in,
            "token_expires": self.token_expires,
            "token_response_keys": self.token_response_keys,
            "auth_error": self.auth_error,
            "auth_error_guidance": self.auth_error_guidance,
            "endpoints": [endpoint.as_dict() for endpoint in self.endpoints],
        }


def _classify_top_level(body: Any) -> str:
    if body is None:
        return "null"
    if isinstance(body, Mapping):
        return "object"
    if isinstance(body, str | bytes):
        return "string"
    if isinstance(body, Sequence):
        return "array"
    return type(body).__name__


def _locate_records(body: Any) -> tuple[str | None, list[Any] | None]:
    """Find the record array, if there is one.

    A bare array is the records. An envelope has them under some key: the well
    known names are tried first, then *any* key holding a list — because the
    envelope key might be something nobody guessed, and finding it is the point.
    """
    if isinstance(body, Sequence) and not isinstance(body, str | bytes):
        return None, list(body)

    if not isinstance(body, Mapping):
        return None, None

    for key in RECORD_CONTAINER_CANDIDATES:
        value = body.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return key, list(value)

    for key, value in body.items():
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return str(key), list(value)

    return None, None


def _pagination_candidates(body: Any) -> dict[str, Any]:
    """Top-level keys whose names resemble paging metadata.

    Values are included for scalars: a page number or a total is not sensitive,
    and it is precisely what makes the finding actionable. Anything non-scalar is
    described rather than reproduced.
    """
    if not isinstance(body, Mapping):
        return {}

    found: dict[str, Any] = {}
    for key, value in body.items():
        key_str = str(key)
        if key_str.lower() not in {candidate.lower() for candidate in PAGINATION_KEY_CANDIDATES}:
            continue
        found[key_str] = (
            value if isinstance(value, int | float | bool | type(None)) else sanitize(value)
        )
    return found


def probe_endpoint(
    client: NineyardClient,
    name: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> EndpointReport:
    """Read one endpoint and describe what came back.

    Errors are captured into the report rather than raised: one inaccessible
    endpoint is a finding, not a reason to abandon the run. Discovering that the
    account can read Items but not PurchaseOrders is exactly the kind of thing
    this is for.
    """
    report = EndpointReport(name=name, path=path, requested_params=dict(params or {}))
    started = time.perf_counter()

    try:
        response = client.get(path, params=params)
    except NineyardError as exc:
        report.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        report.status = exc.status_code
        report.error_type = type(exc).__name__
        report.error_message = exc.message
        report.error_guidance = exc.guidance
        _logger.warning(
            "nineyard.probe.endpoint_failed",
            endpoint=name,
            status=exc.status_code,
            error=type(exc).__name__,
        )
        return report

    report.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    report.status = response.status_code
    report.content_type = response.headers.get("content-type")

    try:
        body = response.json()
    except ValueError:
        report.error_type = "NineyardProtocolError"
        report.error_message = (
            f"Response was {report.content_type or 'an unknown type'}, not decodable JSON."
        )
        report.error_guidance = "An HTML body usually means a login redirect or a wrong path."
        return report

    report.top_level_type = _classify_top_level(body)
    if isinstance(body, Mapping):
        report.top_level_keys = sorted(str(key) for key in body)

    container_key, records = _locate_records(body)
    report.record_container_key = container_key
    if records is not None:
        report.record_count = len(records)
        report.record_field_names = field_names(records)
        report.record_field_presence = field_presence(records)

    report.pagination_candidates = _pagination_candidates(body)
    report.sanitized_sample = sanitize(body, sample_size=sample_size)

    _logger.info(
        "nineyard.probe.endpoint_ok",
        endpoint=name,
        status=report.status,
        record_count=report.record_count,
        field_count=len(report.record_field_names),
    )
    return report


def run_probe(
    client: NineyardClient,
    *,
    endpoints: Mapping[str, str] | None = None,
    params: dict[str, Any] | None = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> ProbeReport:
    """Authenticate, then read each endpoint in turn.

    Authentication failure aborts: without a token nothing else can be learned,
    and a run of four identical 401s is noise rather than data.
    """
    report = ProbeReport(base_url=client.config.base_url)

    try:
        token = client.authenticate()
    except NineyardError as exc:
        report.auth_error = f"{type(exc).__name__}: {exc.message}"
        report.auth_error_guidance = exc.guidance
        return report

    report.authenticated = True
    report.token_fingerprint = token.fingerprint
    report.token_expires_in = token.expires_in
    report.token_expires = token.expires
    report.token_response_keys = list(token.response_keys)

    for name, path in (endpoints or READ_ONLY_ENDPOINTS).items():
        endpoint_params = dict(READ_ONLY_ENDPOINT_DEFAULT_PARAMS.get(name, {}))
        endpoint_params.update(params or {})
        report.endpoints.append(
            probe_endpoint(
                client,
                name,
                path,
                params=endpoint_params or None,
                sample_size=sample_size,
            )
        )

    return report
