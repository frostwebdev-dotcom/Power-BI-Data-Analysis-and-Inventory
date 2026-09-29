"""Probe and sanitiser behaviour, against mocked transports only.

The response shapes below are *invented for the test* — several deliberately
different ones, because the whole point of the probe is that Nineyard's actual
shape is unknown. A probe that only worked against the shape someone guessed
would be worthless.
"""

from __future__ import annotations

from typing import Any

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from app.integrations.nineyard.client import AUTH_PATH, NineyardClient, NineyardConfig
from app.integrations.nineyard.probe import (
    READ_ONLY_ENDPOINT_DEFAULT_PARAMS,
    READ_ONLY_ENDPOINTS,
    probe_endpoint,
    run_probe,
)
from app.integrations.nineyard.sanitize import (
    describe_string,
    field_names,
    field_presence,
    sanitize,
)

TOKEN = "an-access-token-that-must-never-be-logged"


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.nineyard.client.time.sleep", lambda _: None)


def make_client(handler: Any) -> NineyardClient:
    config = NineyardConfig(
        base_url="https://backyard.nineyard.test",
        email="probe@example.test",
        password=SecretStr("probe-password"),
        company_id=1,
        max_attempts=1,
    )
    return NineyardClient(config, transport=httpx.MockTransport(handler))


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"accessToken": TOKEN, "expiresIn": 900, "expires": "later"})


def responder(body: Any, status: int = 200, **kwargs: Any) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == AUTH_PATH:
            return token_response()
        return httpx.Response(status, json=body, **kwargs)

    return handler


class TestShapeDiscovery:
    def test_a_bare_array_response_is_understood(self) -> None:
        body = [{"itemId": 1, "sku": "ABC"}, {"itemId": 2, "sku": "DEF"}]

        with make_client(responder(body)) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert report.top_level_type == "array"
        assert report.record_container_key is None
        assert report.record_count == 2
        assert report.record_field_names == ["itemId", "sku"]

    def test_an_enveloped_response_is_understood(self) -> None:
        body = {"data": [{"id": 1}], "totalCount": 57, "page": 1}

        with make_client(responder(body)) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert report.top_level_type == "object"
        assert report.top_level_keys == ["data", "page", "totalCount"]
        assert report.record_container_key == "data"
        assert report.record_count == 1

    def test_an_unexpected_container_key_is_still_found(self) -> None:
        """The envelope key might be one nobody guessed. Finding it is the point."""
        body = {"nineyardResultCollection": [{"id": 1}, {"id": 2}]}

        with make_client(responder(body)) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert report.record_container_key == "nineyardResultCollection"
        assert report.record_count == 2

    def test_sparse_fields_are_reported_with_their_frequency(self) -> None:
        """ "Present in 1 of 3" is what decides whether a column can be NOT NULL."""
        body = [{"id": 1, "upc": "0123"}, {"id": 2}, {"id": 3}]

        with make_client(responder(body)) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert report.record_field_names == ["id", "upc"]
        assert report.record_field_presence == {"id": 3, "upc": 1}

    def test_an_empty_collection_reports_zero_not_an_error(self) -> None:
        with make_client(responder([])) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert report.succeeded
        assert report.record_count == 0
        assert report.record_field_names == []


class TestPaginationCandidates:
    def test_paging_keys_are_reported_as_candidates_with_values(self) -> None:
        """Counts and page numbers are not sensitive, and are the actionable part."""
        body = {"items": [], "totalCount": 1200, "pageSize": 50, "hasMore": True}

        with make_client(responder(body)) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert report.pagination_candidates == {
            "totalCount": 1200,
            "pageSize": 50,
            "hasMore": True,
        }

    def test_nothing_is_invented_when_no_paging_keys_exist(self) -> None:
        with make_client(responder({"items": []})) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert report.pagination_candidates == {}


class TestErrorReporting:
    @pytest.mark.parametrize(
        ("status", "expected_error"),
        [
            (401, "NineyardAuthenticationError"),
            (403, "NineyardPermissionError"),
            (404, "NineyardNotFoundError"),
            (429, "NineyardRateLimitError"),
            (500, "NineyardServerError"),
        ],
    )
    def test_a_failing_endpoint_is_captured_not_raised(
        self, status: int, expected_error: str
    ) -> None:
        """One inaccessible endpoint is a finding, not a reason to abandon the run."""
        with make_client(responder({}, status=status)) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert not report.succeeded
        assert report.error_type == expected_error
        assert report.status == status
        assert report.error_guidance

    def test_a_non_json_response_is_reported_clearly(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            return httpx.Response(
                200, text="<html>login</html>", headers={"content-type": "text/html"}
            )

        with make_client(handler) as client:
            client.authenticate()
            report = probe_endpoint(client, "Items", "/api/Items")

        assert report.error_type == "NineyardProtocolError"
        assert report.content_type is not None
        assert "text/html" in report.content_type

    def test_the_run_continues_past_one_bad_endpoint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            if request.url.path == "/api/Vendors":
                return httpx.Response(403, json={})
            return httpx.Response(200, json=[{"id": 1}])

        with make_client(handler) as client:
            report = run_probe(client)

        assert len(report.endpoints) == len(READ_ONLY_ENDPOINTS)
        failed = [e for e in report.endpoints if not e.succeeded]
        assert [e.name for e in failed] == ["Vendors"]

    def test_the_run_uses_the_observed_pagination_parameters(self) -> None:
        seen: dict[str, dict[str, str]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            seen[request.url.path] = dict(request.url.params)
            return httpx.Response(200, json=[])

        with make_client(handler) as client:
            report = run_probe(client)

        assert all(endpoint.succeeded for endpoint in report.endpoints)
        for name, path in READ_ONLY_ENDPOINTS.items():
            assert seen[path] == {
                key: str(value) for key, value in READ_ONLY_ENDPOINT_DEFAULT_PARAMS[name].items()
            }

    def test_authentication_failure_aborts_the_run(self) -> None:
        """Four identical 401s would be noise, not data."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={})

        with make_client(handler) as client:
            report = run_probe(client)

        assert not report.authenticated
        assert report.endpoints == []
        assert report.auth_error is not None
        assert "NineyardAuthenticationError" in report.auth_error


class TestTokenReporting:
    def test_token_expiry_is_captured_and_the_token_is_not(self) -> None:
        with make_client(responder([])) as client:
            report = run_probe(client)

        assert report.authenticated
        assert report.token_expires_in == 900
        assert report.token_expires == "later"
        assert report.token_fingerprint is not None
        assert TOKEN not in str(report.as_dict())


class TestSanitisation:
    """Requirement 7: samples carry shape, never values."""

    def test_real_values_never_survive_sanitisation(self) -> None:
        body = {
            "data": [
                {
                    "itemId": 998877,
                    "description": "Blue Widget 12-pack",
                    "upc": "012345678905",
                    "vendorName": "Acme Distribution",
                    "active": True,
                }
            ]
        }

        rendered = str(sanitize(body))

        for secret in ("998877", "Blue Widget 12-pack", "012345678905", "Acme Distribution"):
            assert secret not in rendered

    def test_shape_and_types_do_survive(self) -> None:
        """Otherwise the sample would be useless for field mapping."""
        result = sanitize({"data": [{"upc": "012345678905", "active": True, "qty": 4}]})

        record = result["data"][0]
        assert record["upc"] == "<str len=12 digits>"
        assert record["active"] is True  # booleans identify nobody and are informative
        assert record["qty"] == "<int>"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("012345678905", "<str len=12 digits>"),
            ("ABC123", "<str len=6 alphanumeric>"),
            ("Blue Widget, 12 pack", "<str len=20 text>"),
            ("2026-09-08T12:00:00Z", "<str len=20 datetime-like>"),
            ("3f2504e0-4f89-11d3-9a0c-0305e82c3301", "<str uuid>"),
            ("", "<str empty>"),
        ],
    )
    def test_character_class_is_described_without_the_value(
        self, value: str, expected: str
    ) -> None:
        """A digits-only length-12 string is recognisably a UPC without being one."""
        assert describe_string(value) == expected

    def test_sensitive_keys_are_redacted_rather_than_described(self) -> None:
        """Even a description says too much under a key like apiKey."""
        result = sanitize({"apiKey": "sk-live-abcdef", "password": "hunter2", "id": 1})

        assert result["apiKey"] == "***REDACTED***"
        assert result["password"] == "***REDACTED***"
        assert result["id"] == "<int>"

    def test_long_lists_are_truncated_with_a_count(self) -> None:
        result = sanitize([{"id": n} for n in range(50)], sample_size=2)

        assert len(result) == 3
        assert result[-1] == "<... 48 more of 50>"

    def test_deep_nesting_terminates(self) -> None:
        payload: Any = {"leaf": 1}
        for _ in range(40):
            payload = {"nested": payload}

        assert "<max-depth>" in str(sanitize(payload))

    def test_the_saved_sample_contains_no_values(self) -> None:
        """End to end: what would be written to storage/ carries no data."""
        body = {"data": [{"vendorName": "Acme Distribution", "upc": "012345678905"}]}

        with make_client(responder(body)) as client:
            client.authenticate()
            report = probe_endpoint(client, "Vendors", "/api/Vendors")

        serialised = str(report.as_dict())
        assert "Acme Distribution" not in serialised
        assert "012345678905" not in serialised
        # But the field names, which are the point, are all there.
        assert "vendorName" in serialised
        assert "upc" in serialised


class TestFieldHelpers:
    def test_field_names_are_a_union_across_records(self) -> None:
        assert field_names([{"a": 1}, {"b": 2}, {"a": 3, "c": 4}]) == ["a", "b", "c"]

    def test_non_object_records_are_ignored(self) -> None:
        assert field_names([{"a": 1}, "not-an-object", 42]) == ["a"]

    def test_presence_counts_are_per_field(self) -> None:
        assert field_presence([{"a": 1}, {"a": 2, "b": 3}]) == {"a": 2, "b": 1}
