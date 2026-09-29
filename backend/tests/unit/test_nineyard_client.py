"""Nineyard client behaviour, entirely against mocked transports.

**No test here touches the network.** Every one injects an
``httpx2.MockTransport``, so the suite is safe to run anywhere, including in CI
with no Nineyard credentials.

The response bodies used below are constructed for the test. They are *not*
claims about what Nineyard returns — only the token endpoint's three documented
fields are treated as real, and even those are exercised as "might be absent".
"""

from __future__ import annotations

from typing import Any

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.nineyard.client import (
    AUTH_PATH,
    RETRYABLE_STATUSES,
    NineyardClient,
    NineyardConfig,
    fingerprint_token,
)
from app.integrations.nineyard.errors import (
    NineyardAuthenticationError,
    NineyardConfigurationError,
    NineyardError,
    NineyardNotFoundError,
    NineyardPermissionError,
    NineyardProtocolError,
    NineyardRateLimitError,
    NineyardServerError,
    NineyardTransportError,
)

EMAIL = "integration-account@example.test"
PASSWORD = "a-password-that-must-never-be-logged"
TOKEN = "an-access-token-that-must-never-be-logged-in-full"


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff must not make the suite slow."""
    monkeypatch.setattr("app.integrations.nineyard.client.time.sleep", lambda _: None)


def make_config(**overrides: Any) -> NineyardConfig:
    defaults: dict[str, Any] = {
        "base_url": "https://backyard.nineyard.test",
        "email": EMAIL,
        "password": SecretStr(PASSWORD),
        "company_id": 1234,
        "timeout_seconds": 5.0,
        "max_attempts": 3,
    }
    return NineyardConfig(**{**defaults, **overrides})


def token_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"accessToken": TOKEN, "expiresIn": 3600, "expires": "2026-09-08T18:00:00Z"},
    )


def client_with(handler: Any, **config_overrides: Any) -> NineyardClient:
    return NineyardClient(make_config(**config_overrides), transport=httpx.MockTransport(handler))


def test_client_supports_a_socks_proxy_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secured runners may require their outbound SOCKS proxy."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1080")

    client = NineyardClient(make_config())
    client.close()


class TestReadOnlyByConstruction:
    """The safety property, checked structurally rather than by convention."""

    @pytest.mark.parametrize("verb", ["post", "put", "patch", "delete", "request", "send"])
    def test_the_client_exposes_no_mutating_verb(self, verb: str) -> None:
        """There must be no public method that can write to Nineyard."""
        assert not hasattr(NineyardClient, verb), (
            f"NineyardClient.{verb} exists — the read-only guarantee is gone"
        )

    def test_get_is_the_only_public_request_method(self) -> None:
        public = {
            name
            for name in dir(NineyardClient)
            if not name.startswith("_") and callable(getattr(NineyardClient, name, None))
        }

        assert public == {"authenticate", "close", "get"}

    def test_only_the_auth_path_is_ever_posted_to(self) -> None:
        """The single POST is hard-wired; it cannot be redirected elsewhere."""
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            if request.url.path == AUTH_PATH:
                return token_response()
            return httpx.Response(200, json=[])

        with client_with(handler) as client:
            client.authenticate()
            client.get("/api/Items")

        assert seen == [("POST", AUTH_PATH), ("GET", "/api/Items")]
        assert [method for method, _ in seen if method != "GET"] == ["POST"]


class TestAuthentication:
    def test_a_token_is_obtained_and_described(self) -> None:
        with client_with(lambda _: token_response()) as client:
            token = client.authenticate()

        assert token.expires_in == 3600
        assert token.expires == "2026-09-08T18:00:00Z"
        assert token.response_keys == ("accessToken", "expires", "expiresIn")
        assert token.fingerprint == fingerprint_token(TOKEN)

    def test_the_credentials_are_sent_in_the_documented_shape(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return token_response()

        with client_with(handler) as client:
            client.authenticate()

        assert captured == {"email": EMAIL, "password": PASSWORD, "companyId": 1234}

    def test_the_token_is_held_as_a_secret(self) -> None:
        """So it cannot be printed by an f-string or a repr."""
        with client_with(lambda _: token_response()) as client:
            token = client.authenticate()

        assert TOKEN not in repr(token)
        assert TOKEN not in str(token.access_token)
        assert token.access_token.get_secret_value() == TOKEN

    def test_the_fingerprint_is_not_the_token(self) -> None:
        fingerprint = fingerprint_token(TOKEN)

        assert fingerprint not in TOKEN
        assert len(fingerprint) == 12

    def test_missing_expiry_fields_are_reported_as_absent(self) -> None:
        """ "Documented" is not "observed" — the probe must survive their absence."""
        response = httpx.Response(200, json={"accessToken": TOKEN})

        with client_with(lambda _: response) as client:
            token = client.authenticate()

        assert token.expires_in is None
        assert token.expires is None
        assert token.response_keys == ("accessToken",)

    def test_a_token_response_without_a_token_is_a_protocol_error(self) -> None:
        response = httpx.Response(200, json={"message": "no token for you"})

        with client_with(lambda _: response) as client, pytest.raises(NineyardProtocolError) as e:
            client.authenticate()

        # The message names the keys that *were* present, which is the finding.
        assert "message" in str(e.value)

    def test_an_html_response_is_a_protocol_error_not_a_crash(self) -> None:
        response = httpx.Response(
            200, text="<html><body>Sign in</body></html>", headers={"content-type": "text/html"}
        )

        with client_with(lambda _: response) as client, pytest.raises(NineyardProtocolError):
            client.authenticate()

    def test_reading_before_authenticating_is_refused(self) -> None:
        with (
            client_with(lambda _: token_response()) as client,
            pytest.raises(NineyardConfigurationError),
        ):
            client.get("/api/Items")


class TestStatusHandling:
    """Requirement 10: each status gets its own actionable answer."""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, NineyardAuthenticationError),
            (403, NineyardPermissionError),
            (404, NineyardNotFoundError),
        ],
    )
    def test_definite_failures_map_to_their_own_error(
        self, status: int, expected: type[NineyardError]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            return httpx.Response(status, json={})

        with client_with(handler) as client:
            client.authenticate()
            with pytest.raises(expected) as caught:
                client.get("/api/Items")

        assert caught.value.status_code == status
        assert caught.value.guidance

    def test_rate_limiting_reports_retry_after(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})

        with client_with(handler, max_attempts=1) as client:
            client.authenticate()
            with pytest.raises(NineyardRateLimitError) as caught:
                client.get("/api/Items")

        assert caught.value.retry_after_seconds == 7.0

    def test_server_errors_surface_as_server_errors(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            return httpx.Response(503, json={})

        with client_with(handler, max_attempts=1) as client:
            client.authenticate()
            with pytest.raises(NineyardServerError):
                client.get("/api/Items")


class TestRetryPolicy:
    """Requirement 9: transient failures only."""

    def test_a_transient_server_error_is_retried_and_can_succeed(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(503, json={})
            return httpx.Response(200, json=[{"id": 1}])

        with client_with(handler, max_attempts=3) as client:
            client.authenticate()
            response = client.get("/api/Items")

        assert response.status_code == 200
        assert len(attempts) == 3

    def test_a_transport_error_is_retried(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            attempts.append(1)
            if len(attempts) < 2:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json=[])

        with client_with(handler, max_attempts=3) as client:
            client.authenticate()
            client.get("/api/Items")

        assert len(attempts) == 2

    def test_a_transport_error_gives_up_after_the_budget(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with (
            client_with(handler, max_attempts=2) as client,
            pytest.raises(NineyardTransportError) as caught,
        ):
            client.authenticate()

        assert "2 attempts" in str(caught.value)

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
    def test_definite_failures_are_never_retried(self, status: int) -> None:
        """Retrying a 401 just repeats the same mistake more slowly."""
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == AUTH_PATH:
                return token_response()
            attempts.append(1)
            return httpx.Response(status, json={})

        with client_with(handler, max_attempts=3) as client:
            client.authenticate()
            with pytest.raises(Exception):  # noqa: B017 - attempt count is the assertion
                client.get("/api/Items")

        assert len(attempts) == 1, f"HTTP {status} was retried and should not have been"

    def test_the_retryable_set_is_exactly_transient_statuses(self) -> None:
        assert set(RETRYABLE_STATUSES) == {429, 500, 502, 503, 504}
        assert 401 not in RETRYABLE_STATUSES
        assert 404 not in RETRYABLE_STATUSES


class TestConfiguration:
    def test_missing_credentials_are_named_precisely(self) -> None:
        settings = Settings(app_env="test", nineyard_email=None, nineyard_password=None)

        with pytest.raises(NineyardConfigurationError) as caught:
            NineyardConfig.from_settings(settings)

        message = str(caught.value)
        assert "NINEYARD_EMAIL" in message
        assert "NINEYARD_PASSWORD" in message
        assert "NINEYARD_COMPANY_ID" in message

    def test_a_complete_configuration_is_accepted(self) -> None:
        settings = Settings(
            app_env="test",
            nineyard_base_url="https://backyard.nineyard.test/",
            nineyard_email=EMAIL,
            nineyard_password=SecretStr(PASSWORD),
            nineyard_company_id=99,
        )

        config = NineyardConfig.from_settings(settings)

        assert config.base_url == "https://backyard.nineyard.test"  # trailing slash removed
        assert config.company_id == 99

    def test_the_password_stays_a_secret_on_the_config(self) -> None:
        assert PASSWORD not in repr(make_config())


class TestNoCredentialLeaksIntoLogs:
    def test_authentication_logs_omit_every_credential(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Requirement 3, verified against the real logging pipeline."""
        from app.core.logging import configure_logging

        configure_logging(Settings(app_env="test", log_level="DEBUG", log_format="json"))

        with client_with(lambda _: token_response()) as client:
            client.authenticate()

        output = capsys.readouterr().out
        assert EMAIL not in output
        assert PASSWORD not in output
        assert TOKEN not in output
        # The fingerprint is present, and is not the token.
        assert fingerprint_token(TOKEN) in output
