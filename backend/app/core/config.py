"""Application configuration.

All configuration is read from environment variables through this single typed
settings object. No secret, connection string, or credential is ever hardcoded
or committed (CLAUDE.md §4).

Every credential is typed ``SecretStr``, so it cannot be printed by accident:
Pydantic renders it as ``**********`` in reprs, ``str()``, and JSON dumps. Code
that genuinely needs the value asks for it explicitly with
``.get_secret_value()``, which makes each such use greppable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.redaction import redact_mapping

LogFormat = Literal["json", "console"]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent

# Resolved absolutely, because a relative ".env" is read from the working
# directory: running from backend/ would silently miss the repository-root file
# and fall back to defaults. Later entries win, so a backend-local .env can
# override the shared one. In containers there is no file at all and the values
# arrive as real environment variables.
_ENV_FILES = (_REPO_ROOT / ".env", _BACKEND_ROOT / ".env")

# Environment names treated as production for the purpose of refusing insecure
# defaults and hiding error detail.
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})

# The shipped development signing key. Recognised by name so the application can
# refuse to start with it outside development.
INSECURE_DEV_JWT_SECRET = "insecure-development-signing-key-change-me"

# RFC 7518 §3.2: an HS256 key should be at least as long as the hash output.
MIN_JWT_SECRET_LENGTH = 32

# SP-API is served from three regional endpoints. Anything else is a typo that
# would otherwise surface as an unexplained connection failure.
AMAZON_REGIONS = frozenset({"NA", "EU", "FE"})


class Settings(BaseSettings):
    """Typed application settings sourced from the environment."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---------------------------------------------------------
    app_env: str = "local"
    app_name: str = "prms-api"
    api_v1_prefix: str = "/api/v1"

    # --- Logging -------------------------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    # --- Database ------------------------------------------------------------
    # SecretStr: a DSN carries a password, so it is a credential, not config.
    # Overridden by DATABASE_URL in every real environment; the default points
    # at a local PostgreSQL and is deliberately not a working production value.
    database_url: SecretStr = SecretStr("postgresql+psycopg://prms:prms@localhost:5432/prms")

    # --- Authentication ------------------------------------------------------
    # HS256 signing key for the development authentication backend. Replaced
    # wholesale when Microsoft Entra ID is introduced — see docs/security.md.
    auth_jwt_secret: SecretStr = SecretStr(INSECURE_DEV_JWT_SECRET)
    auth_token_ttl_minutes: int = 480
    auth_issuer: str = "prms-dev"
    auth_audience: str = "prms-api"

    # Gates the development token endpoint. Off by default and refused outright
    # in production, so an unset environment cannot silently expose it.
    dev_auth_enabled: bool = False

    # --- Errors --------------------------------------------------------------
    # When true, unhandled errors return the exception type and message to the
    # client. Never enabled in production: forced off by the validator below.
    expose_error_details: bool = False

    # --- Nineyard integration ------------------------------------------------
    # The authentication shape is observed, not assumed: Nineyard issues a bearer
    # token in exchange for an email/password/companyId triple, so there is no
    # API key. Credentials are unset by default; the diagnostic tool refuses to
    # run without them rather than inventing a fallback.
    nineyard_base_url: str = "https://backyard.nineyard.com"
    nineyard_email: str | None = None
    nineyard_password: SecretStr | None = None
    nineyard_company_id: int | None = None
    nineyard_timeout_seconds: float = 30.0
    # Applies to transient failures only — see integrations/nineyard/client.py.
    nineyard_max_attempts: int = 3

    @property
    def has_nineyard_credentials(self) -> bool:
        return (
            self.nineyard_email is not None
            and self.nineyard_password is not None
            and self.nineyard_company_id is not None
        )

    # --- Amazon SP-API (read-only ingestion, ADR 0011) -----------------------
    # Login with Amazon: a long-lived refresh token is exchanged for short-lived
    # access tokens. The client id is an identifier, not a secret, but it is
    # still redacted in logs because it is useless to anyone reading them and
    # names the application. Everything else is SecretStr. Obtained from
    # Seller Central > Apps & Services > Develop Apps (self-authorised app).
    amazon_enabled: bool = False
    amazon_lwa_client_id: str | None = None
    amazon_lwa_client_secret: SecretStr | None = None
    amazon_lwa_refresh_token: SecretStr | None = None
    amazon_seller_id: str | None = None
    # ATVPDKIKX0DER is amazon.com.
    amazon_marketplace_id: str = "ATVPDKIKX0DER"
    amazon_region: str = "NA"
    amazon_timeout_seconds: float = 60.0
    # Applies to transient failures only, as with Nineyard. SP-API throttles
    # aggressively, so the ceiling is higher.
    amazon_max_attempts: int = 5
    # Pause between FBA inventory pages. The endpoint allows ~2 requests per
    # second; 0.6 s keeps a 450-SKU account (≈ 9 pages of 50) comfortably
    # under it without waiting for a 429 to say so.
    amazon_inventory_page_delay_s: float = 0.6

    # Scheduling (app/jobs/amazon.py). Intervals in minutes; the orders window
    # in days is split into report-sized (30-day) chunks by the job. A RUNNING
    # run older than the timeout is assumed dead and closed as FAILED before
    # a new one starts.
    amazon_orders_interval_minutes: int = 1440
    amazon_inventory_interval_minutes: int = 60
    amazon_listings_interval_minutes: int = 1440
    amazon_orders_window_days: int = 35
    amazon_run_timeout_minutes: int = 120
    # Which organization the configured Amazon account belongs to. Optional
    # while exactly one organization exists; required after that.
    amazon_organization_slug: str | None = None

    @property
    def amazon_configured(self) -> bool:
        """True only when every credential the LWA exchange needs is present."""
        return not self._missing_amazon_settings()

    def _missing_amazon_settings(self) -> list[str]:
        required = {
            "AMAZON_LWA_CLIENT_ID": self.amazon_lwa_client_id,
            "AMAZON_LWA_CLIENT_SECRET": self.amazon_lwa_client_secret,
            "AMAZON_LWA_REFRESH_TOKEN": self.amazon_lwa_refresh_token,
            "AMAZON_SELLER_ID": self.amazon_seller_id,
        }
        return [
            name
            for name, value in required.items()
            if value is None or not _secret_or_str(value).strip()
        ]

    # --- HTTP ----------------------------------------------------------------
    # NoDecode stops pydantic-settings from JSON-decoding the environment value
    # before validation. Without it, a plain CORS_ALLOW_ORIGINS=http://host
    # raises a JSONDecodeError at startup, because list[str] is treated as a
    # complex field and parsed as JSON first.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- Storage (retained import files, ADR 0004) ---------------------------
    storage_raw_dir: str = "/storage/raw"
    storage_processed_dir: str = "/storage/processed"
    storage_rejected_dir: str = "/storage/rejected"
    #: Uploads larger than this are refused with 413 before anything is stored.
    import_max_upload_mb: int = Field(default=50, ge=1, le=1024)

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in PRODUCTION_ENVIRONMENTS

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept ``a,b,c`` from the environment as well as a JSON list."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_database_scheme(cls, value: object) -> object:
        """Accept the DSN shape managed platforms actually hand out.

        Railway, Render, Heroku and friends inject ``postgres://`` or
        ``postgresql://``. SQLAlchemy reads the scheme to pick a driver, and
        without an explicit ``+psycopg`` it reaches for psycopg2, which is not
        installed — so the app dies at first connection with a confusing
        ModuleNotFoundError rather than anything about configuration.

        Rewriting the scheme here means the platform's variable can be used
        as-is, with no manual re-assembly of the DSN per environment.
        """
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw, str):
            return value

        for prefix in ("postgresql+", "postgres+"):
            if raw.startswith(prefix):
                return value  # A driver is already named; leave it alone.

        for prefix in ("postgresql://", "postgres://"):
            if raw.startswith(prefix):
                return SecretStr("postgresql+psycopg://" + raw[len(prefix) :])

        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("amazon_region", mode="before")
    @classmethod
    def _require_a_known_amazon_region(cls, value: object) -> object:
        if isinstance(value, str):
            region = value.strip().upper()
            if region not in AMAZON_REGIONS:
                raise ValueError(
                    f"AMAZON_REGION must be one of {sorted(AMAZON_REGIONS)}, got {value!r}"
                )
            return region
        return value

    @field_validator("auth_jwt_secret")
    @classmethod
    def _require_a_strong_signing_key(cls, value: SecretStr) -> SecretStr:
        """HS256 keys shorter than 32 bytes are weak (RFC 7518 §3.2)."""
        if len(value.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(f"AUTH_JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _refuse_insecure_production_configuration(self) -> Settings:
        """Fail fast rather than run production on development defaults.

        Each of these is a configuration mistake that is invisible at runtime
        until it is exploited, so the application refuses to start instead.
        """
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.auth_jwt_secret.get_secret_value() == INSECURE_DEV_JWT_SECRET:
            problems.append("AUTH_JWT_SECRET is still the shipped development key")
        if self.dev_auth_enabled:
            problems.append("DEV_AUTH_ENABLED must be false in production")
        if self.expose_error_details:
            problems.append("EXPOSE_ERROR_DETAILS must be false in production")

        if problems:
            raise ValueError(f"Refusing to start in {self.app_env!r}: " + "; ".join(problems))
        return self

    @model_validator(mode="after")
    def _refuse_enabled_but_unconfigured_amazon(self) -> Settings:
        """Fail at start-up, not at the first scheduled ingestion hours later.

        Applies in every environment: a half-configured integration is a
        mistake wherever it happens, and the message names exactly what is
        missing so the fix does not need a debugger.
        """
        if not self.amazon_enabled:
            return self
        missing = self._missing_amazon_settings()
        if missing:
            raise ValueError(
                "Refusing to start: AMAZON_ENABLED is true but the integration is not "
                "configured; missing " + ", ".join(missing)
            )
        return self

    def safe_dump(self) -> dict[str, Any]:
        """A representation safe to log or return from a diagnostics endpoint.

        Secrets are already masked by ``SecretStr``; the redaction pass catches
        anything sensitive that arrived under a plain string field.
        """
        return redact_mapping(self.model_dump(mode="json"))


def _secret_or_str(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so the environment is read once. Tests clear the cache when they need
    to exercise a different configuration.
    """
    return Settings()
