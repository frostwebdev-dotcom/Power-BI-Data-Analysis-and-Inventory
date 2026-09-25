"""Read-only Amazon SP-API client — the anti-corruption layer over
``python-amazon-sp-api``.

**This client cannot write to Amazon, structurally.** It exposes exactly five
operations: request a report, read a report's status, download a report
document, the convenience that chains those three, and an iterator over FBA
inventory summaries. Requesting a report is the one non-GET call SP-API needs
for a read (``POST /reports/2021-06-30/reports``) and it creates nothing in
the seller account beyond a queued export. There is no feeds, listings,
pricing or orders-write surface here, no generic ``call(method, path)``, and
no way to reach the wrapped library's other endpoints through this object. A
test asserts the public method set.

**Credentials cross one boundary.** The library wants a dict with the keys
``refresh_token``, ``lwa_app_id`` and ``lwa_client_secret``. That dict is
built in :meth:`AmazonClient._credentials`, handed to the library constructor,
and referenced nowhere else — it is not stored on the client, not logged, and
not returned. Only :class:`Settings` holds the ``SecretStr`` values.

**Retries** cover exactly what could succeed next time: the library's
throttling exception (429) and its 500/503/504 exceptions, with capped
exponential backoff and jitter, up to ``AMAZON_MAX_ATTEMPTS``. An LWA failure
is never retried — the same credentials give the same answer.

The library reads ``SP_API_DEFAULT_MARKETPLACE`` from the environment and
lets it **override** an explicitly passed marketplace. This module refuses to
construct while that variable is set, so the marketplace can only come from
``Settings``.
"""

from __future__ import annotations

import hashlib
import os
import random
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, TypeVar

import httpx
from pydantic import SecretStr
from sp_api.api import Inventories, Reports
from sp_api.auth.exceptions import AuthorizationError
from sp_api.base import Marketplaces, MissingCredentials
from sp_api.base.exceptions import (
    SellingApiException,
    SellingApiGatewayTimeoutException,
    SellingApiRequestThrottledException,
    SellingApiServerException,
    SellingApiTemporarilyUnavailableException,
)

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.amazon.dtos import (
    InventorySummary,
    PayloadShapeError,
    ReportRequest,
    ReportStatus,
)
from app.integrations.amazon.errors import (
    AmazonAuthError,
    AmazonConfigurationError,
    AmazonError,
    AmazonRateLimited,
    AmazonReportFailed,
    AmazonTransientError,
)

_logger = get_logger(__name__)

T = TypeVar("T")

#: The library's exceptions worth retrying: throttled, and the three 5xx
#: classes it distinguishes. Everything else is a definite answer.
RETRYABLE_EXCEPTIONS: Final[tuple[type[SellingApiException], ...]] = (
    SellingApiRequestThrottledException,
    SellingApiServerException,
    SellingApiTemporarilyUnavailableException,
    SellingApiGatewayTimeoutException,
)

#: Upper bound on any single backoff wait.
MAX_BACKOFF_SECONDS: Final = 60.0

# Amazon occasionally invalidates an inventory continuation token during a
# long full-catalog traversal. Because callers receive nothing until a complete
# traversal succeeds, one restart from page 1 is safe and cannot duplicate a
# persisted snapshot.
MAX_INVENTORY_TRAVERSAL_ATTEMPTS: Final = 2
INVALID_NEXT_TOKEN_MESSAGE: Final = "next token is invalid or expired"

# Amazon inventory nextTokens expire 30 seconds after they are created. A
# request timeout longer than that makes retrying the same page guaranteed to
# fail with an expired token after a slow/broken response. Keep page attempts
# below the token lifetime; report generation and document downloads continue
# to use the operator-configured timeout.
INVENTORY_REQUEST_TIMEOUT_SECONDS: Final = 20.0

#: SP-API region → the AWS region string the library's Marketplaces carry.
REGION_TO_AWS: Final[Mapping[str, str]] = {
    "NA": "us-east-1",
    "EU": "eu-west-1",
    "FE": "us-west-2",
}

#: The environment variable the library would let override our marketplace.
LIBRARY_MARKETPLACE_OVERRIDE: Final = "SP_API_DEFAULT_MARKETPLACE"


@dataclass(frozen=True, slots=True)
class CredentialCheck:
    """The outcome of a Login with Amazon exchange, minus the token."""

    expires_in_seconds: int | None
    token_fingerprint: str


@dataclass(frozen=True, slots=True)
class AmazonConfig:
    """Everything the client needs, resolved from application settings."""

    lwa_client_id: str
    lwa_client_secret: SecretStr
    lwa_refresh_token: SecretStr
    seller_id: str
    marketplace: Marketplaces
    region: str
    timeout_seconds: float = 60.0
    max_attempts: int = 5

    @classmethod
    def from_settings(cls, settings: Settings) -> AmazonConfig:
        """Build from settings, or explain precisely what is wrong."""
        missing = settings._missing_amazon_settings()
        if missing:
            raise AmazonConfigurationError(f"Missing required settings: {', '.join(missing)}")

        assert settings.amazon_lwa_client_id is not None
        assert settings.amazon_lwa_client_secret is not None
        assert settings.amazon_lwa_refresh_token is not None
        assert settings.amazon_seller_id is not None

        marketplace = resolve_marketplace(settings.amazon_marketplace_id, settings.amazon_region)

        return cls(
            lwa_client_id=settings.amazon_lwa_client_id,
            lwa_client_secret=settings.amazon_lwa_client_secret,
            lwa_refresh_token=settings.amazon_lwa_refresh_token,
            seller_id=settings.amazon_seller_id,
            marketplace=marketplace,
            region=settings.amazon_region,
            timeout_seconds=settings.amazon_timeout_seconds,
            max_attempts=settings.amazon_max_attempts,
        )


def resolve_marketplace(marketplace_id: str, region: str) -> Marketplaces:
    """Find the library's marketplace for an id, and check it lives in ``region``.

    A UK marketplace id with ``AMAZON_REGION=NA`` would otherwise be sent to
    the North American endpoint and fail with an unhelpful 400.
    """
    matches = [m for m in Marketplaces if m.marketplace_id == marketplace_id]
    if not matches:
        raise AmazonConfigurationError(
            f"AMAZON_MARKETPLACE_ID {marketplace_id!r} is not a marketplace the SP-API "
            "library knows."
        )
    marketplace = matches[0]
    expected_aws_region = REGION_TO_AWS.get(region)
    if expected_aws_region is None or marketplace.region != expected_aws_region:
        raise AmazonConfigurationError(
            f"AMAZON_MARKETPLACE_ID {marketplace_id!r} ({marketplace.name}) is served from "
            f"{marketplace.region}, which is not the {region!r} region."
        )
    return marketplace


class AmazonClient:
    """A deliberately minimal, read-only SP-API client."""

    def __init__(
        self,
        config: AmazonConfig,
        *,
        reports_class: type[Reports] = Reports,
        inventories_class: type[Inventories] = Inventories,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """The library classes, ``sleep`` and ``clock`` are injectable so tests
        never touch the network and never wait."""
        if os.environ.get(LIBRARY_MARKETPLACE_OVERRIDE):
            raise AmazonConfigurationError(
                f"{LIBRARY_MARKETPLACE_OVERRIDE} is set in the environment. The SP-API "
                "library would let it override AMAZON_MARKETPLACE_ID; unset it so the "
                "marketplace comes only from application settings."
            )
        self._config = config
        self._reports_class = reports_class
        self._inventories_class = inventories_class
        self._sleep = sleep
        self._clock = clock

    @property
    def config(self) -> AmazonConfig:
        return self._config

    # -- the credential boundary --------------------------------------------

    def _credentials(self) -> dict[str, str]:
        """The one place the secret values are read.

        Returned only to :meth:`_api`, which passes the dict straight to the
        library constructor. Nothing stores it and nothing logs it.
        """
        return {
            "refresh_token": self._config.lwa_refresh_token.get_secret_value(),
            "lwa_app_id": self._config.lwa_client_id,
            "lwa_client_secret": self._config.lwa_client_secret.get_secret_value(),
        }

    def _api(self, api_class: type[T], *, timeout_seconds: float | None = None) -> T:
        return api_class(  # type: ignore[call-arg]
            marketplace=self._config.marketplace,
            credentials=self._credentials(),
            timeout=self._config.timeout_seconds if timeout_seconds is None else timeout_seconds,
        )

    # -- credentials ----------------------------------------------------------

    def check_credentials(self) -> CredentialCheck:
        """Exchange the refresh token for an access token and report its lifetime.

        The token itself never leaves this method: what comes back is how long
        the token is valid for and a short hash prefix that distinguishes two
        exchanges in a log without being usable as a credential.
        """
        response = self._call_response(
            "LWA token exchange", lambda: self._api(self._reports_class).auth
        )
        expires_in = getattr(response, "expires_in", None)
        token = getattr(response, "access_token", None)
        if not isinstance(token, str) or not token:
            raise AmazonAuthError("LWA token exchange returned no access token")
        return CredentialCheck(
            expires_in_seconds=int(expires_in) if isinstance(expires_in, int | float) else None,
            token_fingerprint=hashlib.sha256(token.encode()).hexdigest()[:12],
        )

    # -- reports -------------------------------------------------------------

    def request_report(
        self,
        report_type: str,
        data_start: datetime | None = None,
        data_end: datetime | None = None,
        report_options: Mapping[str, str] | None = None,
    ) -> ReportRequest:
        """Ask Amazon to generate a report. Returns the id to poll."""
        if (data_start is None) != (data_end is None):
            raise ValueError("data_start and data_end must be provided together")
        if data_start is not None and data_end is not None:
            _require_utc(data_start, "data_start")
            _require_utc(data_end, "data_end")
            if data_end < data_start:
                raise ValueError("data_end must not be before data_start")

        body: dict[str, Any] = {
            "reportType": report_type,
            "marketplaceIds": [self._config.marketplace.marketplace_id],
        }
        if data_start is not None and data_end is not None:
            body["dataStartTime"] = data_start
            body["dataEndTime"] = data_end
        if report_options:
            body["reportOptions"] = dict(report_options)

        _logger.info(
            "amazon.report.requesting",
            report_type=report_type,
            data_start=data_start.isoformat() if data_start is not None else None,
            data_end=data_end.isoformat() if data_end is not None else None,
            marketplace_id=self._config.marketplace.marketplace_id,
        )
        payload = self._call(
            f"createReport {report_type}",
            lambda: self._api(self._reports_class).create_report(**body),
        )
        request = self._parse(ReportRequest, payload, where="createReport")
        _logger.info(
            "amazon.report.requested", report_type=report_type, report_id=request.report_id
        )
        return request

    def get_report_status(self, report_id: str) -> ReportStatus:
        payload = self._call(
            f"getReport {report_id}",
            lambda: self._api(self._reports_class).get_report(report_id),
        )
        status = self._parse(ReportStatus, payload, where="getReport")
        _logger.debug(
            "amazon.report.status",
            report_id=report_id,
            processing_status=status.processing_status,
            has_document=status.report_document_id is not None,
        )
        return status

    def download_report_document(self, report_document_id: str) -> bytes:
        """Fetch and return a finished report's content.

        The library downloads the document, gunzips it when Amazon compressed
        it, and decodes it to text using the charset Amazon declared. That
        text is returned here as UTF-8 bytes, so callers get one stable
        encoding regardless of what the report was served in.
        """
        payload = self._call(
            f"getReportDocument {report_document_id}",
            lambda: self._api(self._reports_class).get_report_document(
                report_document_id, download=True
            ),
        )
        document = payload.get("document") if isinstance(payload, Mapping) else None
        if not isinstance(document, str):
            shape = sorted(payload) if isinstance(payload, Mapping) else type(payload).__name__
            raise AmazonError(
                f"getReportDocument {report_document_id}: the library returned no decoded "
                f"document; payload shape: {shape}"
            )
        content = document.encode("utf-8")
        _logger.info(
            "amazon.report.downloaded",
            report_document_id=report_document_id,
            bytes=len(content),
        )
        return content

    def fetch_report(
        self,
        report_type: str,
        data_start: datetime | None = None,
        data_end: datetime | None = None,
        report_options: Mapping[str, str] | None = None,
        *,
        poll_interval_s: float = 15.0,
        timeout_s: float = 1800.0,
    ) -> bytes:
        """Request, wait for, and download a report in one call.

        Raises :class:`AmazonReportFailed` if Amazon reports ``CANCELLED`` or
        ``FATAL``, or if the report is not ``DONE`` within ``timeout_s``.
        """
        request = self.request_report(report_type, data_start, data_end, report_options)
        deadline = self._clock() + timeout_s
        status = self.get_report_status(request.report_id)

        while not status.is_terminal:
            if self._clock() >= deadline:
                raise AmazonReportFailed(
                    f"Report {request.report_id} ({report_type}) was still "
                    f"{status.processing_status} after {timeout_s:.0f}s.",
                    report_id=request.report_id,
                    processing_status=status.processing_status,
                    timed_out=True,
                )
            self._sleep(poll_interval_s)
            status = self.get_report_status(request.report_id)

        if not status.is_done or status.report_document_id is None:
            raise AmazonReportFailed(
                f"Report {request.report_id} ({report_type}) ended {status.processing_status}.",
                report_id=request.report_id,
                processing_status=status.processing_status,
            )
        return self.download_report_document(status.report_document_id)

    # -- inventory -----------------------------------------------------------

    def iter_inventory_summaries(
        self, *, details: bool = True, page_delay_s: float = 0.0
    ) -> Iterator[InventorySummary]:
        """Every FBA inventory summary for the marketplace, following ``nextToken``.

        ``details=True`` asks for the ``inventoryDetails`` block, which is
        where the inbound quantities live; without it every inbound field is
        ``None``.

        ``page_delay_s`` is slept *between* pages (never before the first or
        after the last). The retry policy only reacts to throttling after it
        happens; a small pause keeps a large account under the endpoint's
        ~2 requests/second in the first place.
        """
        if page_delay_s < 0:
            raise ValueError("page_delay_s must not be negative")

        for traversal_attempt in range(1, MAX_INVENTORY_TRAVERSAL_ATTEMPTS + 1):
            try:
                summaries = self._load_inventory_summaries(
                    details=details, page_delay_s=page_delay_s
                )
            except AmazonError as exc:
                token_expired = INVALID_NEXT_TOKEN_MESSAGE in exc.message.lower()
                if not token_expired or traversal_attempt == MAX_INVENTORY_TRAVERSAL_ATTEMPTS:
                    raise
                _logger.warning(
                    "amazon.inventory.pagination_restarting",
                    traversal_attempt=traversal_attempt,
                    reason="continuation token invalid or expired",
                )
                self._sleep(max(page_delay_s, 1.0))
                continue

            yield from summaries
            return

    def _load_inventory_summaries(
        self, *, details: bool, page_delay_s: float
    ) -> list[InventorySummary]:
        """Buffer one traversal so a restart never yields duplicate pages."""
        next_token: str | None = None
        page = 0
        summaries: list[InventorySummary] = []
        while True:
            page += 1
            if page > 1 and page_delay_s:
                self._sleep(page_delay_s)
            params: dict[str, Any] = {
                "details": details,
                "marketplaceIds": [self._config.marketplace.marketplace_id],
            }
            if next_token:
                params["nextToken"] = next_token

            token_for_call = next_token
            response = self._call_response(
                f"getInventorySummaries page {page}",
                self._inventory_page_call(params),
            )
            payload = response.payload if isinstance(response.payload, Mapping) else {}
            items = payload.get("inventorySummaries")
            if not isinstance(items, list):
                raise AmazonError(
                    f"getInventorySummaries page {page}: expected 'inventorySummaries' list; "
                    f"keys present: {sorted(payload)}"
                )
            _logger.info(
                "amazon.inventory.page", page=page, items=len(items), had_token=bool(token_for_call)
            )
            for item in items:
                if not isinstance(item, Mapping):
                    raise AmazonError(
                        f"getInventorySummaries page {page}: item was {type(item).__name__}"
                    )
                summaries.append(self._parse(InventorySummary, item, where="inventorySummaries"))

            next_token = response.next_token if isinstance(response.next_token, str) else None
            if not next_token:
                return summaries

    def _inventory_page_call(self, params: Mapping[str, Any]) -> Callable[[], Any]:
        """Bind one page's parameters so the retry loop re-sends the same page."""
        frozen = dict(params)

        def call() -> Any:
            timeout = min(self._config.timeout_seconds, INVENTORY_REQUEST_TIMEOUT_SECONDS)
            return self._api(
                self._inventories_class, timeout_seconds=timeout
            ).get_inventory_summary_marketplace(**frozen)

        return call

    # -- internals -----------------------------------------------------------

    def _call(self, describe: str, operation: Callable[[], Any]) -> Any:
        """Run a library call with retries and return its ``payload``."""
        return self._call_response(describe, operation).payload

    def _call_response(self, describe: str, operation: Callable[[], Any]) -> Any:
        """Run a library call, retrying only what could succeed next time."""
        attempts = self._config.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                return operation()
            except AuthorizationError as exc:
                raise AmazonAuthError(
                    f"{describe}: Login with Amazon refused the credentials "
                    f"({getattr(exc, 'error_code', 'unknown')}).",
                    status_code=getattr(exc, "status_code", None),
                ) from None
            except MissingCredentials as exc:
                raise AmazonConfigurationError(f"{describe}: {exc}") from None
            except RETRYABLE_EXCEPTIONS as exc:
                if attempt == attempts:
                    raise _exhausted(describe, exc, attempts) from None
                self._sleep_before_retry(
                    attempt,
                    describe=describe,
                    reason=f"HTTP {_status(exc)}",
                    retry_after=_retry_after(exc),
                )
            except httpx.TransportError as exc:
                # python-amazon-sp-api lets httpx transport failures cross its
                # boundary. These are indeterminate network failures rather
                # than definite SP-API answers, so retry the same operation.
                if attempt == attempts:
                    raise AmazonTransientError(
                        f"{describe}: {type(exc).__name__} on every one of "
                        f"{attempts} attempts."
                    ) from None
                self._sleep_before_retry(
                    attempt,
                    describe=describe,
                    reason=type(exc).__name__,
                    retry_after=None,
                )
            except SellingApiException as exc:
                raise AmazonError(
                    f"{describe}: SP-API returned {_status(exc)}: {_message(exc)}",
                    status_code=_status(exc),
                ) from None
        raise AssertionError("unreachable")  # pragma: no cover

    def _sleep_before_retry(
        self,
        attempt: int,
        *,
        describe: str,
        reason: str,
        retry_after: float | None,
    ) -> None:
        """Exponential backoff with jitter, or Amazon's own Retry-After, capped."""
        if retry_after is not None:
            delay = min(retry_after, MAX_BACKOFF_SECONDS)
        else:
            delay = min(2.0 ** (attempt - 1), MAX_BACKOFF_SECONDS)
            delay *= 0.5 + random.random() / 2
        _logger.warning(
            "amazon.retrying",
            request=describe,
            attempt=attempt,
            reason=reason,
            delay_seconds=round(delay, 2),
        )
        self._sleep(delay)

    @staticmethod
    def _parse(dto: type[T], payload: Any, *, where: str) -> T:
        if not isinstance(payload, Mapping):
            raise AmazonError(f"{where}: expected an object, got {type(payload).__name__}")
        try:
            return dto.from_payload(payload)  # type: ignore[attr-defined, no-any-return]
        except PayloadShapeError as exc:
            raise AmazonError(str(exc)) from None


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{name} must be a timezone-aware UTC datetime")


def _status(exc: SellingApiException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _message(exc: SellingApiException) -> str:
    message = getattr(exc, "message", None)
    return message if isinstance(message, str) else type(exc).__name__


def _retry_after(exc: SellingApiException) -> float | None:
    headers = getattr(exc, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _exhausted(describe: str, exc: SellingApiException, attempts: int) -> AmazonError:
    if isinstance(exc, SellingApiRequestThrottledException):
        return AmazonRateLimited(
            f"{describe}: throttled on every one of {attempts} attempts.",
            retry_after_seconds=_retry_after(exc),
        )
    return AmazonTransientError(
        f"{describe}: SP-API returned {_status(exc)} on every one of {attempts} attempts.",
        status_code=_status(exc),
    )
