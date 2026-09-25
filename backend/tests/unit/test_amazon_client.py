"""Read-only Amazon SP-API client (ADR 0011).

The library classes are replaced with fakes that record how they were
constructed and return scripted responses, so these tests never open a socket
and never wait on a real clock. The realistic payloads are shaped after the
documented SP-API models; they are fixtures, not observations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import pytest
from pydantic import SecretStr
from sp_api.auth.exceptions import AuthorizationError
from sp_api.base import ApiResponse, Marketplaces
from sp_api.base.exceptions import (
    SellingApiBadRequestException,
    SellingApiRequestThrottledException,
    SellingApiServerException,
)

from app.core.config import Settings
from app.core.logging import configure_logging
from app.integrations.amazon import (
    AmazonAuthError,
    AmazonClient,
    AmazonConfig,
    AmazonConfigurationError,
    AmazonError,
    AmazonRateLimited,
    AmazonReportFailed,
    AmazonTransientError,
    InventorySummary,
    ReportRequest,
    ReportStatus,
    resolve_marketplace,
)
from app.integrations.amazon.client import LIBRARY_MARKETPLACE_OVERRIDE

CLIENT_ID = "amzn1.application-oa2-client.test"
CLIENT_SECRET = "amzn1.oa2-cs.v1.d0-not-leak-this-client-secret"
REFRESH_TOKEN = "Atzr|d0-not-leak-this-refresh-token-0123456789abcdef"
SELLER_ID = "A1TESTSELLER"
ALL_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN)

START = datetime(2026, 8, 16, tzinfo=UTC)
END = datetime(2026, 9, 15, tzinfo=UTC)


# --- fakes ---------------------------------------------------------------------


class Recorder:
    """Shared between a fake class and the test that installed it."""

    def __init__(self) -> None:
        self.constructions: list[dict[str, Any]] = []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.script: list[Callable[[], Any]] = []

    def next(self) -> Any:
        if not self.script:
            raise AssertionError("fake called more times than scripted")
        return self.script.pop(0)()


def returns(payload: Any, *, next_token: str | None = None) -> Callable[[], ApiResponse]:
    return lambda: ApiResponse(payload=payload, nextToken=next_token)


def raises(exc: Exception) -> Callable[[], Any]:
    def _raise() -> Any:
        raise exc

    return _raise


def throttled(retry_after: str | None = None) -> SellingApiRequestThrottledException:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return SellingApiRequestThrottledException(
        [{"code": "QuotaExceeded", "message": "You exceeded your quota"}], headers
    )


def server_error() -> SellingApiServerException:
    return SellingApiServerException([{"code": "InternalFailure", "message": "boom"}], {})


def make_fakes(recorder: Recorder) -> tuple[type, type]:
    class FakeReports:
        def __init__(self, **kwargs: Any) -> None:
            recorder.constructions.append(kwargs)

        def create_report(self, **kwargs: Any) -> Any:
            recorder.calls.append(("create_report", (), kwargs))
            return recorder.next()

        def get_report(self, report_id: str, **kwargs: Any) -> Any:
            recorder.calls.append(("get_report", (report_id,), kwargs))
            return recorder.next()

        def get_report_document(self, document_id: str, **kwargs: Any) -> Any:
            recorder.calls.append(("get_report_document", (document_id,), kwargs))
            return recorder.next()

        @property
        def auth(self) -> Any:
            recorder.calls.append(("auth", (), {}))
            return recorder.next()

    class FakeInventories:
        def __init__(self, **kwargs: Any) -> None:
            recorder.constructions.append(kwargs)

        def get_inventory_summary_marketplace(self, **kwargs: Any) -> Any:
            recorder.calls.append(("get_inventory_summary_marketplace", (), kwargs))
            return recorder.next()

    return FakeReports, FakeInventories


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def config() -> AmazonConfig:
    return AmazonConfig(
        lwa_client_id=CLIENT_ID,
        lwa_client_secret=SecretStr(CLIENT_SECRET),
        lwa_refresh_token=SecretStr(REFRESH_TOKEN),
        seller_id=SELLER_ID,
        marketplace=Marketplaces.US,
        region="NA",
        timeout_seconds=12.5,
        max_attempts=3,
    )


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def client(config: AmazonConfig, recorder: Recorder, clock: FakeClock) -> AmazonClient:
    reports, inventories = make_fakes(recorder)
    return AmazonClient(
        config,
        reports_class=reports,
        inventories_class=inventories,
        sleep=clock.sleep,
        clock=clock,
    )


# --- realistic payloads -------------------------------------------------------

CREATE_REPORT_PAYLOAD = {"reportId": "1234567890123"}

GET_REPORT_DONE = {
    "reportId": "1234567890123",
    "reportType": "GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA",
    "dataStartTime": "2026-08-16T00:00:00+00:00",
    "dataEndTime": "2026-09-15T00:00:00+00:00",
    "marketplaceIds": ["ATVPDKIKX0DER"],
    "createdTime": "2026-09-15T10:00:00+00:00",
    "processingStatus": "DONE",
    "processingStartTime": "2026-09-15T10:00:05+00:00",
    "processingEndTime": "2026-09-15T10:01:00+00:00",
    "reportDocumentId": "amzn1.spdoc.1.4.na.abc123",
}

GET_REPORT_QUEUED = {**GET_REPORT_DONE, "processingStatus": "IN_QUEUE"}
GET_REPORT_QUEUED.pop("reportDocumentId")
GET_REPORT_IN_PROGRESS = {**GET_REPORT_QUEUED, "processingStatus": "IN_PROGRESS"}
GET_REPORT_CANCELLED = {**GET_REPORT_QUEUED, "processingStatus": "CANCELLED"}
GET_REPORT_FATAL = {**GET_REPORT_QUEUED, "processingStatus": "FATAL"}

DOCUMENT_TEXT = "sku\tasin\tquantity\nWIDGET-12\tB000TEST01\t42\n"
REPORT_DOCUMENT_PAYLOAD = {
    "reportDocumentId": "amzn1.spdoc.1.4.na.abc123",
    "url": "https://tortuga-prod-na.s3-external-1.amazonaws.com/...",
    "compressionAlgorithm": "GZIP",
    "document": DOCUMENT_TEXT,
}

INVENTORY_ITEM_FULL = {
    "asin": "B000TEST01",
    "fnSku": "X000TEST01",
    "sellerSku": "WIDGET-12",
    "condition": "NewItem",
    "inventoryDetails": {
        "fulfillableQuantity": 40,
        "inboundWorkingQuantity": 100,
        "inboundShippedQuantity": 50,
        "inboundReceivingQuantity": 10,
        "reservedQuantity": {
            "totalReservedQuantity": 2,
            "pendingCustomerOrderQuantity": 1,
            "pendingTransshipmentQuantity": 0,
            "fcProcessingQuantity": 1,
        },
        "researchingQuantity": {
            "totalResearchingQuantity": 0,
            "researchingQuantityBreakdown": [],
        },
        "unfulfillableQuantity": {
            "totalUnfulfillableQuantity": 3,
            "customerDamagedQuantity": 3,
        },
    },
    "lastUpdatedTime": "2026-09-15T09:30:00Z",
    "productName": "Blue Widget, 12 pack",
    "totalQuantity": 205,
}

INVENTORY_ITEM_MINIMAL = {
    "asin": "B000TEST02",
    "fnSku": "X000TEST02",
    "sellerSku": "GADGET-1",
    "condition": "NewItem",
    "lastUpdatedTime": "",
    "productName": "Gadget",
    "totalQuantity": 0,
}


def inventory_page(items: list[dict[str, Any]], next_token: str | None = None) -> ApiResponse:
    payload: dict[str, Any] = {
        "granularity": {"granularityType": "Marketplace", "granularityId": "ATVPDKIKX0DER"},
        "inventorySummaries": items,
    }
    if next_token:
        payload["pagination"] = {"nextToken": next_token}
    return ApiResponse(payload=payload)


# --- structure -----------------------------------------------------------------


class TestReadOnlyStructure:
    FORBIDDEN_PREFIXES: ClassVar[tuple[str, ...]] = (
        "create_",
        "put_",
        "post_",
        "update_",
        "delete_",
        "submit_",
    )

    def public_methods(self) -> set[str]:
        return {
            name
            for name in dir(AmazonClient)
            if not name.startswith("_") and callable(getattr(AmazonClient, name, None))
        }

    def test_no_public_method_looks_like_a_write(self) -> None:
        offenders = {
            name
            for name in self.public_methods()
            if name.startswith(self.FORBIDDEN_PREFIXES) and name != "request_report"
        }

        assert offenders == set(), f"write-shaped methods on AmazonClient: {sorted(offenders)}"

    def test_the_public_surface_is_exactly_the_reads(self) -> None:
        assert self.public_methods() == {
            "check_credentials",
            "request_report",
            "get_report_status",
            "download_report_document",
            "fetch_report",
            "iter_inventory_summaries",
        }

    def test_there_is_no_generic_call_method(self) -> None:
        for name in ("call", "request", "get", "post", "put", "delete", "patch"):
            assert not hasattr(AmazonClient, name)


# --- configuration and the credential boundary --------------------------------


class TestConfiguration:
    def full_settings(self, **overrides: Any) -> Settings:
        return Settings(
            amazon_lwa_client_id=CLIENT_ID,
            amazon_lwa_client_secret=SecretStr(CLIENT_SECRET),
            amazon_lwa_refresh_token=SecretStr(REFRESH_TOKEN),
            amazon_seller_id=SELLER_ID,
            **overrides,
        )

    def test_config_from_settings(self) -> None:
        settings = self.full_settings(amazon_timeout_seconds=7.0, amazon_max_attempts=2)

        config = AmazonConfig.from_settings(settings)

        assert config.marketplace is Marketplaces.US
        assert config.region == "NA"
        assert config.seller_id == SELLER_ID
        assert config.timeout_seconds == 7.0
        assert config.max_attempts == 2
        assert config.lwa_client_secret.get_secret_value() == CLIENT_SECRET

    def test_missing_credentials_are_named(self) -> None:
        with pytest.raises(AmazonConfigurationError, match="AMAZON_LWA_REFRESH_TOKEN"):
            AmazonConfig.from_settings(Settings(amazon_lwa_client_id=CLIENT_ID))

    def test_marketplace_is_resolved_by_id(self) -> None:
        assert resolve_marketplace("A1F83G8C2ARO7P", "EU") is Marketplaces.GB
        assert resolve_marketplace("A1VC38T7YXB528", "FE") is Marketplaces.JP

    def test_unknown_marketplace_id_is_refused(self) -> None:
        with pytest.raises(AmazonConfigurationError, match="not a marketplace"):
            resolve_marketplace("NOTAMARKETPLACE", "NA")

    def test_marketplace_in_the_wrong_region_is_refused(self) -> None:
        """A UK marketplace sent to the NA endpoint would fail with an opaque 400."""
        with pytest.raises(AmazonConfigurationError, match="not the 'NA' region"):
            AmazonConfig.from_settings(self.full_settings(amazon_marketplace_id="A1F83G8C2ARO7P"))

    def test_the_library_environment_override_is_refused(
        self, config: AmazonConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The library would silently prefer this variable over our settings."""
        monkeypatch.setenv(LIBRARY_MARKETPLACE_OVERRIDE, "GB")

        with pytest.raises(AmazonConfigurationError, match=LIBRARY_MARKETPLACE_OVERRIDE):
            AmazonClient(config)

    def test_config_repr_does_not_leak_secrets(self, config: AmazonConfig) -> None:
        rendered = repr(config)

        for secret in ALL_SECRETS:
            assert secret not in rendered


class TestCredentialBoundary:
    def test_the_library_receives_exactly_the_three_keys(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns(CREATE_REPORT_PAYLOAD)]

        client.request_report("GET_MERCHANT_LISTINGS_ALL_DATA", START, END)

        [construction] = recorder.constructions
        assert construction["credentials"] == {
            "refresh_token": REFRESH_TOKEN,
            "lwa_app_id": CLIENT_ID,
            "lwa_client_secret": CLIENT_SECRET,
        }
        assert construction["marketplace"] is Marketplaces.US
        assert construction["timeout"] == 12.5

    def test_the_client_does_not_hold_the_credentials_dict(self, client: AmazonClient) -> None:
        """Only SecretStr values live on the object; the plain dict is transient."""
        for value in vars(client).values():
            assert not isinstance(value, dict) or "refresh_token" not in value
        assert REFRESH_TOKEN not in repr(vars(client))

    def test_no_secret_reaches_any_log_line(
        self, client: AmazonClient, recorder: Recorder, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every log line the client emits, plus a DEBUG line from the library's
        logger carrying the credentials dict, is scrubbed before output."""
        configure_logging(Settings(log_level="DEBUG", log_format="json"))
        recorder.script = [
            raises(throttled()),
            returns(CREATE_REPORT_PAYLOAD),
            returns(GET_REPORT_DONE),
            returns(REPORT_DOCUMENT_PAYLOAD),
        ]

        client.fetch_report("GET_MERCHANT_LISTINGS_ALL_DATA", START, END, poll_interval_s=0)
        # What the library would do if its debug logging were switched on.
        logging.getLogger("sp_api.base.client").debug(
            "credentials %s", recorder.constructions[0]["credentials"]
        )
        logging.getLogger("sp_api.auth").debug(
            '{"access_token": "Atza|IwEBIsecretaccess0123456789"}'
        )

        out = capsys.readouterr().out
        for secret in ALL_SECRETS:
            assert secret not in out
        assert "Atza|IwEBIsecretaccess0123456789" not in out
        # The lines themselves still arrived — it is the values that are gone.
        assert "amazon.report.requested" in out
        assert "amazon.retrying" in out


class TestCheckCredentials:
    def test_reports_expiry_and_a_fingerprint_but_never_the_token(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        token = "Atza|IwEBIExampleAccessToken_abcdefghijklmnopqrstuvwxyz0123456789"
        recorder.script = [lambda: type("Resp", (), {"access_token": token, "expires_in": 3600})()]

        check = client.check_credentials()

        assert check.expires_in_seconds == 3600
        assert len(check.token_fingerprint) == 12
        assert token not in repr(check)
        assert recorder.calls == [("auth", (), {})]

    def test_an_lwa_failure_is_an_auth_error(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [raises(AuthorizationError("invalid_client", "bad secret", 401))]

        with pytest.raises(AmazonAuthError, match="invalid_client"):
            client.check_credentials()


# --- DTO mapping ---------------------------------------------------------------


class TestReportRequest:
    def test_request_report_sends_the_documented_body_and_maps_the_id(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns(CREATE_REPORT_PAYLOAD)]

        request = client.request_report(
            "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL",
            START,
            END,
            report_options={"custom": "true"},
        )

        assert request == ReportRequest(report_id="1234567890123")
        [(name, _, kwargs)] = recorder.calls
        assert name == "create_report"
        assert kwargs == {
            "reportType": "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL",
            "dataStartTime": START,
            "dataEndTime": END,
            "marketplaceIds": ["ATVPDKIKX0DER"],
            "reportOptions": {"custom": "true"},
        }

    def test_report_options_are_omitted_when_not_given(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns(CREATE_REPORT_PAYLOAD)]

        client.request_report("GET_MERCHANT_LISTINGS_ALL_DATA", START, END)

        assert "reportOptions" not in recorder.calls[0][2]

    def test_snapshot_report_can_omit_a_data_window(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns(CREATE_REPORT_PAYLOAD)]

        client.request_report("GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA")

        assert recorder.calls[0][2] == {
            "reportType": "GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA",
            "marketplaceIds": ["ATVPDKIKX0DER"],
        }

    def test_a_partial_data_window_is_refused(self, client: AmazonClient) -> None:
        with pytest.raises(ValueError, match="provided together"):
            client.request_report("X", START)

    def test_naive_datetimes_are_refused(self, client: AmazonClient) -> None:
        with pytest.raises(ValueError, match="timezone-aware UTC"):
            client.request_report("X", datetime(2026, 8, 1), END)

    def test_inverted_window_is_refused(self, client: AmazonClient) -> None:
        with pytest.raises(ValueError, match="data_end must not be before"):
            client.request_report("X", END, START)

    def test_a_payload_without_report_id_is_a_shape_error(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns({"unexpected": "shape"})]

        with pytest.raises(AmazonError, match="reportId"):
            client.request_report("X", START, END)


class TestReportStatus:
    def test_done_maps_status_and_document_id(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns(GET_REPORT_DONE)]

        status = client.get_report_status("1234567890123")

        assert status == ReportStatus(
            processing_status="DONE", report_document_id="amzn1.spdoc.1.4.na.abc123"
        )
        assert status.is_done and status.is_terminal
        assert recorder.calls[0][1] == ("1234567890123",)

    def test_queued_has_no_document_yet(self, client: AmazonClient, recorder: Recorder) -> None:
        recorder.script = [returns(GET_REPORT_QUEUED)]

        status = client.get_report_status("1234567890123")

        assert status.processing_status == "IN_QUEUE"
        assert status.report_document_id is None
        assert not status.is_terminal


class TestReportDocument:
    def test_download_returns_the_decoded_document_as_utf8_bytes(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns(REPORT_DOCUMENT_PAYLOAD)]

        content = client.download_report_document("amzn1.spdoc.1.4.na.abc123")

        assert content == DOCUMENT_TEXT.encode("utf-8")
        [(name, args, kwargs)] = recorder.calls
        assert (name, args) == ("get_report_document", ("amzn1.spdoc.1.4.na.abc123",))
        assert kwargs == {"download": True}

    def test_a_missing_document_is_an_error_not_empty_bytes(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns({"reportDocumentId": "x", "url": "https://..."})]

        with pytest.raises(AmazonError, match="no decoded document"):
            client.download_report_document("x")


class TestInventorySummaryMapping:
    def test_full_payload_maps_every_field(self) -> None:
        summary = InventorySummary.from_payload(INVENTORY_ITEM_FULL)

        assert summary == InventorySummary(
            seller_sku="WIDGET-12",
            asin="B000TEST01",
            fnsku="X000TEST01",
            condition="NewItem",
            last_updated=datetime(2026, 9, 15, 9, 30, tzinfo=UTC),
            total=205,
            fulfillable=40,
            inbound_working=100,
            inbound_shipped=50,
            inbound_receiving=10,
            reserved_total=2,
            unfulfillable_total=3,
            researching_total=0,
        )

    def test_minimal_payload_leaves_omitted_quantities_none(self) -> None:
        """Without details=true Amazon omits the block; None, never a fake zero."""
        summary = InventorySummary.from_payload(INVENTORY_ITEM_MINIMAL)

        assert summary.seller_sku == "GADGET-1"
        assert summary.total == 0
        assert summary.last_updated is None
        assert summary.fulfillable is None
        assert summary.inbound_working is None
        assert summary.reserved_total is None

    def test_a_boolean_is_not_a_quantity(self) -> None:
        summary = InventorySummary.from_payload({**INVENTORY_ITEM_MINIMAL, "totalQuantity": True})

        assert summary.total is None

    def test_seller_sku_is_required(self) -> None:
        with pytest.raises(AmazonError, match="sellerSku"):
            AmazonClient._parse(InventorySummary, {"asin": "B000"}, where="inventorySummaries")

    def test_dtos_are_frozen(self) -> None:
        summary = InventorySummary.from_payload(INVENTORY_ITEM_FULL)

        with pytest.raises(AttributeError):
            summary.total = 1  # type: ignore[misc]


# --- pagination ----------------------------------------------------------------


class TestInventoryPagination:
    def test_inventory_page_timeout_stays_below_next_token_lifetime(
        self, config: AmazonConfig, recorder: Recorder, clock: FakeClock
    ) -> None:
        reports, inventories = make_fakes(recorder)
        long_timeout = AmazonConfig(
            lwa_client_id=config.lwa_client_id,
            lwa_client_secret=config.lwa_client_secret,
            lwa_refresh_token=config.lwa_refresh_token,
            seller_id=config.seller_id,
            marketplace=config.marketplace,
            region=config.region,
            timeout_seconds=60.0,
            max_attempts=config.max_attempts,
        )
        client = AmazonClient(
            long_timeout,
            reports_class=reports,
            inventories_class=inventories,
            sleep=clock.sleep,
            clock=clock,
        )
        recorder.script = [lambda: inventory_page([])]

        list(client.iter_inventory_summaries())

        assert recorder.constructions[0]["timeout"] == 20.0

    def test_three_pages_are_followed_and_each_item_yielded_once(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        def page(sku: str, token: str | None = None) -> ApiResponse:
            return inventory_page([{**INVENTORY_ITEM_MINIMAL, "sellerSku": sku}], token)

        recorder.script = [
            lambda: page("SKU-1", "tok-2"),
            lambda: page("SKU-2", "tok-3"),
            lambda: page("SKU-3"),
        ]

        skus = [s.seller_sku for s in client.iter_inventory_summaries()]

        assert skus == ["SKU-1", "SKU-2", "SKU-3"]
        tokens = [kwargs.get("nextToken") for _, _, kwargs in recorder.calls]
        assert tokens == [None, "tok-2", "tok-3"]
        # details and the marketplace go on every page.
        assert all(kwargs["details"] is True for _, _, kwargs in recorder.calls)
        assert all(kwargs["marketplaceIds"] == ["ATVPDKIKX0DER"] for _, _, kwargs in recorder.calls)

    def test_a_page_delay_is_slept_between_pages_only(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        """Not before the first page, not after the last: two sleeps for three pages."""
        recorder.script = [
            lambda: inventory_page([INVENTORY_ITEM_MINIMAL], "tok-2"),
            lambda: inventory_page([INVENTORY_ITEM_MINIMAL], "tok-3"),
            lambda: inventory_page([INVENTORY_ITEM_MINIMAL]),
        ]

        list(client.iter_inventory_summaries(page_delay_s=0.6))

        assert clock.sleeps == [0.6, 0.6]

    def test_no_page_delay_by_default(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [
            lambda: inventory_page([INVENTORY_ITEM_MINIMAL], "tok-2"),
            lambda: inventory_page([INVENTORY_ITEM_MINIMAL]),
        ]

        list(client.iter_inventory_summaries())

        assert clock.sleeps == []

    def test_a_negative_page_delay_is_refused(self, client: AmazonClient) -> None:
        with pytest.raises(ValueError, match="page_delay_s"):
            list(client.iter_inventory_summaries(page_delay_s=-1))

    def test_details_can_be_switched_off(self, client: AmazonClient, recorder: Recorder) -> None:
        recorder.script = [lambda: inventory_page([])]

        assert list(client.iter_inventory_summaries(details=False)) == []
        assert recorder.calls[0][2]["details"] is False

    def test_a_page_without_the_list_is_a_shape_error(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [returns({"granularity": {}})]

        with pytest.raises(AmazonError, match="inventorySummaries"):
            list(client.iter_inventory_summaries())

    def test_a_throttled_page_is_retried_with_the_same_token(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [
            lambda: inventory_page([INVENTORY_ITEM_MINIMAL], "tok-2"),
            raises(throttled()),
            lambda: inventory_page([INVENTORY_ITEM_FULL]),
        ]

        skus = [s.seller_sku for s in client.iter_inventory_summaries()]

        assert skus == ["GADGET-1", "WIDGET-12"]
        assert [k.get("nextToken") for _, _, k in recorder.calls] == [None, "tok-2", "tok-2"]
        assert len(clock.sleeps) == 1

    def test_an_expired_next_token_restarts_once_without_duplicate_items(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        expired = SellingApiBadRequestException(
            [{"code": "InvalidInput", "message": "Next token is invalid or expired"}], {}
        )
        recorder.script = [
            lambda: inventory_page(
                [{**INVENTORY_ITEM_MINIMAL, "sellerSku": "STALE-PAGE"}], "old-token"
            ),
            raises(expired),
            lambda: inventory_page(
                [{**INVENTORY_ITEM_MINIMAL, "sellerSku": "FRESH-1"}], "new-token"
            ),
            lambda: inventory_page([{**INVENTORY_ITEM_MINIMAL, "sellerSku": "FRESH-2"}]),
        ]

        skus = [summary.seller_sku for summary in client.iter_inventory_summaries()]

        assert skus == ["FRESH-1", "FRESH-2"]
        assert [kwargs.get("nextToken") for _, _, kwargs in recorder.calls] == [
            None,
            "old-token",
            None,
            "new-token",
        ]
        assert clock.sleeps == [1.0]


# --- retry policy --------------------------------------------------------------


class TestRetryPolicy:
    def test_throttled_then_success(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [raises(throttled()), returns(CREATE_REPORT_PAYLOAD)]

        request = client.request_report("X", START, END)

        assert request.report_id == "1234567890123"
        assert len(recorder.calls) == 2
        assert len(clock.sleeps) == 1
        assert 0 < clock.sleeps[0] <= 1.0  # first backoff: 2**0 with jitter

    def test_server_error_then_success(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [raises(server_error()), returns(GET_REPORT_DONE)]

        assert client.get_report_status("r").is_done
        assert len(clock.sleeps) == 1

    def test_transport_disconnect_then_success(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [
            raises(httpx.RemoteProtocolError("server disconnected")),
            returns(GET_REPORT_DONE),
        ]

        assert client.get_report_status("r").is_done
        assert len(recorder.calls) == 2
        assert len(clock.sleeps) == 1

    def test_retry_after_is_honoured_and_capped(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [
            raises(throttled("5")),
            raises(throttled("999")),
            returns(CREATE_REPORT_PAYLOAD),
        ]

        client.request_report("X", START, END)

        assert clock.sleeps == [5.0, 60.0]

    def test_backoff_grows_and_is_capped(
        self, config: AmazonConfig, recorder: Recorder, clock: FakeClock
    ) -> None:
        wide = AmazonConfig(**{**vars_of(config), "max_attempts": 9})
        reports, inventories = make_fakes(recorder)
        client = AmazonClient(
            wide,
            reports_class=reports,
            inventories_class=inventories,
            sleep=clock.sleep,
            clock=clock,
        )
        recorder.script = [raises(server_error()) for _ in range(8)] + [returns(GET_REPORT_DONE)]

        client.get_report_status("r")

        # 2**(n-1) * jitter in [0.5, 1.0), capped at 60.
        for attempt, delay in enumerate(clock.sleeps, start=1):
            ceiling = min(2.0 ** (attempt - 1), 60.0)
            assert ceiling * 0.5 <= delay <= ceiling

    def test_throttling_exhausts_into_rate_limited(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [raises(throttled("2")) for _ in range(3)]

        with pytest.raises(AmazonRateLimited, match="every one of 3 attempts") as excinfo:
            client.request_report("X", START, END)

        assert excinfo.value.retry_after_seconds == 2.0
        assert len(recorder.calls) == 3
        assert len(clock.sleeps) == 2  # no sleep after the final failure

    def test_server_errors_exhaust_into_transient_error(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        recorder.script = [raises(server_error()) for _ in range(3)]

        with pytest.raises(AmazonTransientError) as excinfo:
            client.get_report_status("r")

        assert excinfo.value.status_code == 500

    def test_transport_disconnect_exhausts_into_transient_error(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [
            raises(httpx.RemoteProtocolError("server disconnected")) for _ in range(3)
        ]

        with pytest.raises(AmazonTransientError, match="RemoteProtocolError"):
            client.get_report_status("r")

        assert len(recorder.calls) == 3
        assert len(clock.sleeps) == 2

    def test_auth_errors_are_never_retried(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [raises(AuthorizationError("invalid_grant", "bad refresh token", 400))]

        with pytest.raises(AmazonAuthError, match="invalid_grant") as excinfo:
            client.request_report("X", START, END)

        assert len(recorder.calls) == 1
        assert clock.sleeps == []
        assert excinfo.value.status_code == 400
        assert REFRESH_TOKEN not in str(excinfo.value)

    def test_a_definite_4xx_is_not_retried(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [
            raises(
                SellingApiBadRequestException(
                    [{"code": "InvalidInput", "message": "reportType is invalid"}], {}
                )
            )
        ]

        with pytest.raises(AmazonError, match="reportType is invalid") as excinfo:
            client.request_report("NOT_A_REPORT", START, END)

        assert excinfo.value.status_code == 400
        assert clock.sleeps == []
        assert not isinstance(excinfo.value, AmazonTransientError | AmazonRateLimited)


def vars_of(config: AmazonConfig) -> dict[str, Any]:
    return {field.name: getattr(config, field.name) for field in fields(config)}


# --- fetch_report polling ------------------------------------------------------


class TestFetchReport:
    def test_polls_until_done_then_downloads(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [
            returns(CREATE_REPORT_PAYLOAD),
            returns(GET_REPORT_QUEUED),
            returns(GET_REPORT_IN_PROGRESS),
            returns(GET_REPORT_DONE),
            returns(REPORT_DOCUMENT_PAYLOAD),
        ]

        content = client.fetch_report(
            "GET_MERCHANT_LISTINGS_ALL_DATA", START, END, poll_interval_s=15
        )

        assert content == DOCUMENT_TEXT.encode("utf-8")
        assert [name for name, _, _ in recorder.calls] == [
            "create_report",
            "get_report",
            "get_report",
            "get_report",
            "get_report_document",
        ]
        assert clock.sleeps == [15, 15]

    @pytest.mark.parametrize("terminal", [GET_REPORT_CANCELLED, GET_REPORT_FATAL])
    def test_cancelled_and_fatal_raise_report_failed(
        self, client: AmazonClient, recorder: Recorder, terminal: dict[str, Any]
    ) -> None:
        recorder.script = [returns(CREATE_REPORT_PAYLOAD), returns(terminal)]

        with pytest.raises(AmazonReportFailed) as excinfo:
            client.fetch_report("X", START, END, poll_interval_s=0)

        assert excinfo.value.report_id == "1234567890123"
        assert excinfo.value.processing_status == terminal["processingStatus"]
        assert excinfo.value.timed_out is False
        # Nothing was downloaded.
        assert "get_report_document" not in [name for name, _, _ in recorder.calls]

    def test_timeout_raises_report_failed_with_timed_out(
        self, client: AmazonClient, recorder: Recorder, clock: FakeClock
    ) -> None:
        recorder.script = [returns(CREATE_REPORT_PAYLOAD)] + [
            returns(GET_REPORT_IN_PROGRESS) for _ in range(50)
        ]

        with pytest.raises(AmazonReportFailed, match="still IN_PROGRESS after 60s") as excinfo:
            client.fetch_report("X", START, END, poll_interval_s=15, timeout_s=60)

        assert excinfo.value.timed_out is True
        assert excinfo.value.processing_status == "IN_PROGRESS"
        # Polled at t=0, 15, 30, 45, 60 → the fifth poll is at the deadline.
        assert clock.sleeps == [15, 15, 15, 15]

    def test_done_without_a_document_id_is_a_failure(
        self, client: AmazonClient, recorder: Recorder
    ) -> None:
        done_without_doc = {**GET_REPORT_DONE}
        done_without_doc.pop("reportDocumentId")
        recorder.script = [returns(CREATE_REPORT_PAYLOAD), returns(done_without_doc)]

        with pytest.raises(AmazonReportFailed):
            client.fetch_report("X", START, END, poll_interval_s=0)


# --- error taxonomy ------------------------------------------------------------


class TestErrorTaxonomy:
    @pytest.mark.parametrize(
        "error_class",
        [
            AmazonConfigurationError,
            AmazonAuthError,
            AmazonRateLimited,
            AmazonTransientError,
            AmazonReportFailed,
        ],
    )
    def test_every_error_is_an_amazon_error_with_guidance(self, error_class: type) -> None:
        assert issubclass(error_class, AmazonError)
        assert error_class.guidance and error_class.guidance != AmazonError.guidance

    def test_report_failed_carries_its_context(self) -> None:
        error = AmazonReportFailed("x", report_id="r1", processing_status="FATAL")

        assert (error.report_id, error.processing_status, error.timed_out) == ("r1", "FATAL", False)
