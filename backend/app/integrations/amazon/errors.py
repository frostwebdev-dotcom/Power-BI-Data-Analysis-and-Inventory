"""Amazon SP-API error taxonomy.

The library raises a dozen exception classes keyed on HTTP status, plus a
separate one for Login with Amazon failures. Callers of this package do not
need that granularity; they need to know one thing — can this be retried, and
if not, whose problem is it? Five classes answer that, and nothing above the
anti-corruption layer ever sees a library exception.
"""

from __future__ import annotations


class AmazonError(Exception):
    """Base for every Amazon integration failure."""

    #: Short, actionable guidance for whoever reads the log or the CLI output.
    guidance: str = "Inspect the error and retry."

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AmazonConfigurationError(AmazonError):
    """Credentials, marketplace or region are missing or inconsistent."""

    guidance = (
        "Set AMAZON_LWA_CLIENT_ID, AMAZON_LWA_CLIENT_SECRET, AMAZON_LWA_REFRESH_TOKEN "
        "and AMAZON_SELLER_ID in .env, and make sure AMAZON_MARKETPLACE_ID belongs "
        "to AMAZON_REGION. They are never read from anywhere else."
    )


class AmazonAuthError(AmazonError):
    """Login with Amazon rejected the credentials, or the token was refused.

    Never retried: the same credentials produce the same answer.
    """

    guidance = (
        "The LWA exchange failed or SP-API refused the access token. Verify the "
        "client id, client secret and refresh token, and that the application is "
        "still authorised for this seller in Seller Central."
    )


class AmazonRateLimited(AmazonError):  # noqa: N818 — name fixed by ADR 0011's brief
    """429 — throttled, and still throttled after the retry budget."""

    guidance = (
        "Throttled by SP-API after every retry. The Orders and Reports endpoints "
        "have low quotas; slow the schedule down rather than raising the attempt "
        "count."
    )

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = 429,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after_seconds = retry_after_seconds


class AmazonTransientError(AmazonError):
    """A 5xx or transport failure persisted through every retry."""

    guidance = (
        "SP-API returned a server error or the connection failed on every attempt. "
        "The next scheduled run will try again."
    )


class AmazonReportFailed(AmazonError):  # noqa: N818 — name fixed by ADR 0011's brief
    """A requested report ended CANCELLED or FATAL, or never finished in time."""

    guidance = (
        "Amazon did not produce the report. CANCELLED usually means no data for "
        "the window or a duplicate request; FATAL means the report type or "
        "options were rejected. A timeout means the queue is slow — try later."
    )

    def __init__(
        self,
        message: str,
        *,
        report_id: str,
        processing_status: str | None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.report_id = report_id
        self.processing_status = processing_status
        self.timed_out = timed_out
