"""Configuration behaviour (AC-0.2)."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings


def test_cors_origins_accept_comma_separated_string() -> None:
    settings = Settings(cors_allow_origins="http://a.test, http://b.test")

    assert settings.cors_allow_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_accept_a_list() -> None:
    settings = Settings(cors_allow_origins=["http://a.test"])

    assert settings.cors_allow_origins == ["http://a.test"]


def test_empty_cors_origins_yield_an_empty_list() -> None:
    settings = Settings(cors_allow_origins="")

    assert settings.cors_allow_origins == []


def test_log_level_is_normalised_to_upper_case() -> None:
    assert Settings(log_level="debug").log_level == "DEBUG"


def test_settings_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://from-env.test")

    settings = Settings()

    assert settings.app_env == "staging"
    assert settings.log_level == "WARNING"
    assert settings.cors_allow_origins == ["http://from-env.test"]


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_no_credential_is_hardcoded_as_a_working_default() -> None:
    """The default database URL must be an obvious local placeholder.

    This is a tripwire, not proof: it fails loudly if someone ever pastes a real
    host into the default (CLAUDE.md §4).
    """
    default_url = Settings.model_fields["database_url"].default

    assert isinstance(default_url, SecretStr)
    assert "localhost" in default_url.get_secret_value()


class TestDatabaseUrlNormalisation:
    """Managed platforms inject a DSN without a driver name.

    Railway, Render and Heroku all hand out `postgres://` or `postgresql://`.
    SQLAlchemy picks its driver from the scheme, so without `+psycopg` it
    reaches for psycopg2 — which is not installed — and the app dies at first
    connection with a ModuleNotFoundError that says nothing about config.
    """

    @pytest.mark.parametrize(
        "injected",
        ["postgres://u:p@host:5432/db", "postgresql://u:p@host:5432/db"],
    )
    def test_a_driverless_scheme_gains_psycopg(self, injected: str) -> None:
        settings = Settings(database_url=injected)

        assert settings.database_url.get_secret_value() == "postgresql+psycopg://u:p@host:5432/db"

    def test_an_explicit_driver_is_left_alone(self) -> None:
        explicit = "postgresql+asyncpg://u:p@host:5432/db"

        settings = Settings(database_url=explicit)

        assert settings.database_url.get_secret_value() == explicit

    def test_the_password_is_still_masked_after_rewriting(self) -> None:
        settings = Settings(database_url="postgres://u:sup3rs3cret@host:5432/db")

        assert "sup3rs3cret" not in repr(settings)
        assert "sup3rs3cret" in settings.database_url.get_secret_value()

    def test_a_non_postgres_url_is_untouched(self) -> None:
        settings = Settings(database_url="sqlite:///./local.db")

        assert settings.database_url.get_secret_value() == "sqlite:///./local.db"


class TestAmazonSettings:
    """Read-only SP-API configuration (ADR 0011)."""

    FULL: ClassVar[dict[str, Any]] = {
        "amazon_lwa_client_id": "amzn1.application-oa2-client.test",
        "amazon_lwa_client_secret": "amzn1.oa2-cs.v1.test-secret",
        "amazon_lwa_refresh_token": "Atzr|test-refresh-token-value-0123456789",
        "amazon_seller_id": "A1TESTSELLER",
    }

    def test_defaults_are_disabled_and_unconfigured(self) -> None:
        settings = Settings()

        assert settings.amazon_enabled is False
        assert settings.amazon_configured is False
        assert settings.amazon_marketplace_id == "ATVPDKIKX0DER"
        assert settings.amazon_region == "NA"
        assert settings.amazon_timeout_seconds == 60.0
        assert settings.amazon_max_attempts == 5
        assert settings.amazon_inventory_page_delay_s == 0.6

    def test_configured_when_all_four_credentials_are_present(self) -> None:
        assert Settings(**self.FULL).amazon_configured is True

    @pytest.mark.parametrize("missing", sorted(FULL))
    def test_not_configured_when_any_credential_is_absent(self, missing: str) -> None:
        partial = {k: v for k, v in self.FULL.items() if k != missing}

        assert Settings(**partial).amazon_configured is False

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_credential_counts_as_absent(self, blank: str) -> None:
        """An empty environment variable is still set; it must not pass."""
        assert Settings(**{**self.FULL, "amazon_seller_id": blank}).amazon_configured is False

    def test_enabled_without_configuration_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError, match="AMAZON_ENABLED is true") as excinfo:
            Settings(amazon_enabled=True)

        # The message names every missing variable, so the fix is obvious.
        message = str(excinfo.value)
        for name in (
            "AMAZON_LWA_CLIENT_ID",
            "AMAZON_LWA_CLIENT_SECRET",
            "AMAZON_LWA_REFRESH_TOKEN",
            "AMAZON_SELLER_ID",
        ):
            assert name in message

    def test_enabled_with_one_missing_names_only_that_one(self) -> None:
        partial = {k: v for k, v in self.FULL.items() if k != "amazon_seller_id"}

        with pytest.raises(ValidationError, match="missing AMAZON_SELLER_ID") as excinfo:
            Settings(amazon_enabled=True, **partial)

        assert "AMAZON_LWA_CLIENT_ID" not in str(excinfo.value)

    def test_enabled_and_configured_starts(self) -> None:
        settings = Settings(amazon_enabled=True, **self.FULL)

        assert settings.amazon_enabled and settings.amazon_configured

    def test_the_guard_applies_outside_production_too(self) -> None:
        """A half-configured integration is a mistake in any environment."""
        with pytest.raises(ValidationError, match="AMAZON_ENABLED"):
            Settings(app_env="local", amazon_enabled=True)

    def test_settings_read_from_upper_cased_environment_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AMAZON_ENABLED", "true")
        monkeypatch.setenv("AMAZON_LWA_CLIENT_ID", self.FULL["amazon_lwa_client_id"])
        monkeypatch.setenv("AMAZON_LWA_CLIENT_SECRET", self.FULL["amazon_lwa_client_secret"])
        monkeypatch.setenv("AMAZON_LWA_REFRESH_TOKEN", self.FULL["amazon_lwa_refresh_token"])
        monkeypatch.setenv("AMAZON_SELLER_ID", self.FULL["amazon_seller_id"])
        monkeypatch.setenv("AMAZON_MARKETPLACE_ID", "A1F83G8C2ARO7P")
        monkeypatch.setenv("AMAZON_REGION", "eu")
        monkeypatch.setenv("AMAZON_TIMEOUT_SECONDS", "15.5")
        monkeypatch.setenv("AMAZON_MAX_ATTEMPTS", "2")

        settings = Settings()

        assert settings.amazon_enabled is True
        assert settings.amazon_configured is True
        assert settings.amazon_marketplace_id == "A1F83G8C2ARO7P"
        assert settings.amazon_region == "EU"
        assert settings.amazon_timeout_seconds == 15.5
        assert settings.amazon_max_attempts == 2

    @pytest.mark.parametrize("region", ["NA", "eu", " fe "])
    def test_region_is_normalised_to_a_known_endpoint(self, region: str) -> None:
        assert Settings(amazon_region=region).amazon_region == region.strip().upper()

    def test_an_unknown_region_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="AMAZON_REGION must be one of"):
            Settings(amazon_region="us-east-1")


class TestNineyardSettings:
    def test_defaults_are_disabled_and_unconfigured(self) -> None:
        settings = Settings()

        assert settings.nineyard_enabled is False
        assert settings.has_nineyard_credentials is False
        assert settings.nineyard_sku_request_interval_seconds == 1.25
        assert settings.nineyard_sync_interval_minutes == 1440

    def test_enabled_without_credentials_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError, match="NINEYARD_ENABLED is true"):
            Settings(nineyard_enabled=True)

    def test_enabled_with_all_credentials_starts(self) -> None:
        settings = Settings(
            nineyard_enabled=True,
            nineyard_email="catalog@example.test",
            nineyard_password="secret",
            nineyard_company_id=1234,
            nineyard_account="Seller Account",
        )

        assert settings.nineyard_enabled is True
        assert settings.has_nineyard_credentials is True

    def test_enabled_without_a_sku_account_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError, match="NINEYARD_ACCOUNT"):
            Settings(
                nineyard_enabled=True,
                nineyard_email="catalog@example.test",
                nineyard_password="secret",
                nineyard_company_id=1234,
            )
