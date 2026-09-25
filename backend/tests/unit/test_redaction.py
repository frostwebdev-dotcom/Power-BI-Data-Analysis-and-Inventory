"""Log redaction (requirement 6).

The interesting cases are the ones where a credential arrives somewhere nobody
thought to guard: nested in a dict, inside a free-text message, in a DSN logged
by a driver, or on a log record from a third-party library that never touches
structlog.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger
from app.core.redaction import REDACTED, is_sensitive_key, redact_mapping, redact_text


class TestSensitiveKeys:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "Password",
            "user_password",
            "passwd",
            "authorization",
            "Authorization",
            "HTTP_AUTHORIZATION",
            "access_token",
            "refresh_token",
            "id_token",
            "bearer_token",
            "api_key",
            "apiKey",
            "X-API-Key",
            "client_secret",
            "auth_jwt_secret",
            "private_key",
            "credential",
            "Cookie",
            "database_url",
            "dsn",
            # Amazon SP-API / LWA (ADR 0011)
            "refresh_token",
            "REFRESH_TOKEN",
            "amazon_lwa_refresh_token",
            "client_secret",
            "amazon_lwa_client_secret",
            "lwa_client_id",
            "LWA_CLIENT_ID",
            "x-amz-access-token",
            "X-Amz-Access-Token",
        ],
    )
    def test_sensitive_keys_are_recognised(self, key: str) -> None:
        assert is_sensitive_key(key)

    @pytest.mark.parametrize(
        "key",
        [
            "email",
            "user_id",
            "vendor_code",
            "token_type",
            "expires_in",
            "status_code",
            "amazon_seller_id",
            "amazon_marketplace_id",
            "amazon_region",
        ],
    )
    def test_ordinary_keys_are_left_alone(self, key: str) -> None:
        assert not is_sensitive_key(key)


class TestMappingRedaction:
    def test_authorization_header_is_redacted(self) -> None:
        """The headline case: a request-header dump must not leak the token."""
        event = {
            "event": "request.received",
            "headers": {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
                "Content-Type": "application/json",
            },
        }

        redacted = redact_mapping(event)

        assert redacted["headers"]["Authorization"] == REDACTED
        # Non-sensitive neighbours survive, or the log becomes useless.
        assert redacted["headers"]["Content-Type"] == "application/json"
        assert "eyJhbGciOiJIUzI1NiJ9" not in json.dumps(redacted)

    def test_passwords_are_redacted_at_any_depth(self) -> None:
        event = {"user": {"profile": {"password": "hunter2", "email": "a@b.test"}}}

        redacted = redact_mapping(event)

        assert redacted["user"]["profile"]["password"] == REDACTED
        assert redacted["user"]["profile"]["email"] == "a@b.test"

    def test_secrets_inside_lists_are_redacted(self) -> None:
        event = {"attempts": [{"api_key": "sk-live-abc123"}, {"api_key": "sk-live-def456"}]}

        redacted = redact_mapping(event)

        assert all(item["api_key"] == REDACTED for item in redacted["attempts"])
        assert "sk-live" not in json.dumps(redacted)

    def test_a_non_string_secret_is_still_masked(self) -> None:
        """A sensitive key never has a value worth partially preserving."""
        assert redact_mapping({"api_key": 12345})["api_key"] == REDACTED

    def test_a_deeply_nested_structure_terminates(self) -> None:
        """Depth is capped, so a pathological payload cannot hang the logger."""
        event: dict[str, object] = {"level": "leaf"}
        for _ in range(50):
            event = {"nested": event}

        redact_mapping(event)  # must return rather than recurse forever


class TestTextRedaction:
    def test_bearer_token_in_free_text(self) -> None:
        masked = redact_text("calling api with Authorization: Bearer abc123def456ghi")

        assert "abc123def456ghi" not in masked
        assert REDACTED in masked

    def test_bare_jwt_in_free_text(self) -> None:
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmMifQ.Zm9vYmFyc2lnbmF0dXJl"

        masked = redact_text(f"token was {token}")

        assert token not in masked

    def test_database_url_password_is_masked(self) -> None:
        """A DSN in an exception message is a common accidental leak."""
        masked = redact_text(
            "could not connect to postgresql+psycopg://prms:sup3rs3cret@db:5432/prms"
        )

        assert "sup3rs3cret" not in masked
        # The rest stays legible, which is the whole point of masking rather
        # than dropping the message.
        assert "db:5432/prms" in masked

    def test_inline_assignment_is_masked(self) -> None:
        masked = redact_text("retrying with api_key=sk-live-9f8e7d and mode=fast")

        assert "sk-live-9f8e7d" not in masked
        assert "mode=fast" in masked


class TestLoggingPipeline:
    def test_authorization_header_never_reaches_the_output(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """End to end through the real logging configuration."""
        configure_logging(Settings(log_level="INFO", log_format="json"))

        get_logger("test").info(
            "request.received",
            headers={"Authorization": "Bearer super-secret-token-value"},
            password="hunter2",
        )

        line = capsys.readouterr().out.strip()
        payload = json.loads(line)

        assert "super-secret-token-value" not in line
        assert "hunter2" not in line
        assert payload["headers"]["Authorization"] == REDACTED
        assert payload["password"] == REDACTED
        # The event itself is still readable.
        assert payload["event"] == "request.received"

    def test_third_party_library_logs_are_redacted_too(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Records that never pass through structlog still get scrubbed.

        A driver logging its connection string is the realistic version of this,
        and it is exactly the leak that call-site discipline cannot prevent.
        """
        configure_logging(Settings(log_level="INFO", log_format="json"))

        logging.getLogger("some.third.party").info(
            "connecting to postgresql://prms:leaked-password@db:5432/prms"
        )

        out = capsys.readouterr().out
        assert "leaked-password" not in out
        assert REDACTED in out


class TestTokenFingerprintAllowlist:
    """A fingerprint is designed to be logged; redacting it defeats its purpose."""

    def test_a_token_fingerprint_survives_redaction(self) -> None:
        event = {"token_fingerprint": "abc123def456", "event": "nineyard.authenticated"}

        assert redact_mapping(event)["token_fingerprint"] == "abc123def456"

    def test_but_an_actual_token_is_still_redacted(self) -> None:
        """The allowlist is narrow: the neighbouring key is not exempted."""
        event = {"token_fingerprint": "abc123", "access_token": "the-real-thing"}

        redacted = redact_mapping(event)

        assert redacted["token_fingerprint"] == "abc123"
        assert redacted["access_token"] == REDACTED


class TestAmazonRedaction:
    """Login with Amazon token shapes and the SP-API request header (ADR 0011)."""

    ACCESS = "Atza|IwEBIExampleAccessToken_abcdefghijklmnopqrstuvwxyz0123456789"
    REFRESH = "Atzr|IwEBIExampleRefreshToken_abcdefghijklmnopqrstuvwxyz0123456789"

    def test_the_lwa_token_response_body_is_masked(self) -> None:
        """The raw JSON an HTTP library would log on a debug line."""
        body = (
            f'{{"access_token": "{self.ACCESS}", "refresh_token": "{self.REFRESH}", '
            '"token_type": "bearer", "expires_in": 3600}'
        )

        masked = redact_text(body)

        assert self.ACCESS not in masked
        assert self.REFRESH not in masked
        assert '"token_type": "bearer"' in masked
        assert '"expires_in": 3600' in masked

    def test_an_access_token_json_field_is_masked_whatever_its_value(self) -> None:
        """The field name alone is enough; the value need not look like a token."""
        masked = redact_text('{"access_token": "opaque-value-without-prefix", "expires_in": 60}')

        assert "opaque-value-without-prefix" not in masked
        assert masked == f'{{"access_token": "{REDACTED}", "expires_in": 60}}'

    def test_json_field_matching_is_case_insensitive_and_whitespace_tolerant(self) -> None:
        masked = redact_text('{ "Access_Token" :"abc" }')

        assert "abc" not in masked

    @pytest.mark.parametrize("token", [ACCESS, REFRESH])
    def test_a_bare_lwa_token_in_free_text_is_masked(self, token: str) -> None:
        masked = redact_text(f"exchange returned {token} for the seller")

        assert token not in masked
        assert "for the seller" in masked

    def test_the_sp_api_access_token_header_is_masked(self) -> None:
        event = {
            "event": "amazon.request",
            "headers": {
                "x-amz-access-token": self.ACCESS,
                "x-amz-date": "20260915T000000Z",
                "user-agent": "prms/0.1",
            },
        }

        redacted = redact_mapping(event)

        assert redacted["headers"]["x-amz-access-token"] == REDACTED
        assert redacted["headers"]["x-amz-date"] == "20260915T000000Z"
        assert self.ACCESS not in json.dumps(redacted)

    def test_presigned_and_pagination_url_values_are_masked(self) -> None:
        url = (
            "https://example.test/report?nextToken=opaque-page-token"
            "&X-Amz-Credential=temporary-credential"
            "&X-Amz-Signature=temporary-signature"
        )

        masked = redact_text(url)

        assert "opaque-page-token" not in masked
        assert "temporary-credential" not in masked
        assert "temporary-signature" not in masked
        assert masked.count(REDACTED) == 3

    def test_http_client_info_urls_are_not_logged(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(Settings(log_level="INFO", log_format="json"))

        logging.getLogger("httpx").info(
            "HTTP Request: GET https://example.test/?X-Amz-Signature=do-not-log"
        )

        assert capsys.readouterr().out == ""

    def test_lwa_keys_are_masked_at_any_depth(self) -> None:
        event = {"config": {"amazon": {"lwa_client_id": "amzn1.app", "lwa_client_secret": "s"}}}

        redacted = redact_mapping(event)

        assert redacted["config"]["amazon"]["lwa_client_id"] == REDACTED
        assert redacted["config"]["amazon"]["lwa_client_secret"] == REDACTED

    def test_end_to_end_through_the_logging_pipeline(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(Settings(log_level="INFO", log_format="json"))

        get_logger("test").info(
            "lwa.exchange",
            headers={"x-amz-access-token": self.ACCESS},
            body=f'{{"access_token": "{self.ACCESS}"}}',
            note=f"refresh token was {self.REFRESH}",
        )

        line = capsys.readouterr().out
        assert self.ACCESS not in line
        assert self.REFRESH not in line
        assert json.loads(line.strip())["event"] == "lwa.exchange"
