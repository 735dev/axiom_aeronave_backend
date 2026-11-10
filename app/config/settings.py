"""Application settings, read from environment variables (and a local .env file).

No secret has a default value in code. In local/test an ephemeral JWT secret is
generated per process; anywhere else a missing or weak secret aborts startup.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

LOCAL_ENVIRONMENTS = frozenset({"local", "dev", "development", "test"})
MIN_SECRET_LENGTH = 32
KNOWN_PLACEHOLDER_SECRETS = frozenset({"change-me", "changeme", "secret", "dev-secret"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    PROJECT_NAME: str = "Aeronaves Backend"
    VERSION: str = "0.1.0"
    DESCRIPTION: str = "Control de operaciones aereas (Transvalor) - Axiom Core Tech"
    API_V1_STR: str = "/api/v1"

    ENVIRONMENT: str = "local"
    DEBUG: bool = False
    ENABLE_DOCS: bool | None = None

    # Runtime connection. Production uses a role without UPDATE/DELETE on audit_event.
    DATABASE_URL: str = "postgresql+asyncpg://aero:aero@localhost:5440/aero"
    # Owner connection used only by Alembic. Falls back to DATABASE_URL.
    MIGRATION_DATABASE_URL: str | None = None

    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=1)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1)
    BCRYPT_ROUNDS: int = Field(default=12, ge=4, le=15)

    # Comma separated. Never "*": the API is called with bearer tokens.
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # Login throttling. Failures are counted per account and per client IP.
    LOGIN_MAX_FAILS_PER_EMAIL: int = Field(default=5, ge=1)
    LOGIN_MAX_FAILS_PER_IP: int = Field(default=20, ge=1)
    LOGIN_LOCK_SECONDS: int = Field(default=15 * 60, ge=1)
    # Shared store for the limiter. Empty means in-process memory (single replica only).
    REDIS_URL: str | None = None
    REDIS_TIMEOUT_SECONDS: float = 1.0
    # Trust the last X-Forwarded-For hop. Only behind a reverse proxy you control.
    TRUST_FORWARDED_FOR: bool = False

    # Per-action throttling of the costly endpoints (receipt reading, uploads, passenger
    # search). Each action has a budget per account AND per client IP inside one window.
    # Counters live in the same store as the login throttle (REDIS_URL optional).
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_WINDOW_SECONDS: int = Field(default=60, ge=1)
    RATE_LIMIT_EXTRACT_PER_ACCOUNT: int = Field(default=20, ge=1)
    RATE_LIMIT_EXTRACT_PER_IP: int = Field(default=60, ge=1)
    RATE_LIMIT_UPLOAD_PER_ACCOUNT: int = Field(default=60, ge=1)
    RATE_LIMIT_UPLOAD_PER_IP: int = Field(default=180, ge=1)
    RATE_LIMIT_SEARCH_PER_ACCOUNT: int = Field(default=60, ge=1)
    RATE_LIMIT_SEARCH_PER_IP: int = Field(default=180, ge=1)
    # Whole-trail / whole-period CSV exports (audit and reconciliation).
    RATE_LIMIT_EXPORT_PER_ACCOUNT: int = Field(default=10, ge=1)
    RATE_LIMIT_EXPORT_PER_IP: int = Field(default=30, ge=1)
    # Free-text search over the audit trail.
    RATE_LIMIT_IMPORT_PER_ACCOUNT: int = Field(default=10, ge=1)
    RATE_LIMIT_IMPORT_PER_IP: int = Field(default=30, ge=1)
    RATE_LIMIT_AUDIT_SEARCH_PER_ACCOUNT: int = Field(default=60, ge=1)
    RATE_LIMIT_AUDIT_SEARCH_PER_IP: int = Field(default=180, ge=1)
    # Aggregating reads: ticket listing, ticket totals, reconciliation report.
    RATE_LIMIT_HEAVY_LIST_PER_ACCOUNT: int = Field(default=240, ge=1)
    RATE_LIMIT_HEAVY_LIST_PER_IP: int = Field(default=720, ge=1)

    # Idempotency-Key replay cache: how long an answer is remembered, and how often the
    # expired rows are swept by the middleware.
    IDEMPOTENCY_TTL_SECONDS: int = Field(default=7 * 24 * 3600, ge=1)
    IDEMPOTENCY_PURGE_INTERVAL_SECONDS: int = Field(default=600, ge=1)

    # Retention policy (days). 0 disables the purge of that data. The purges are explicit
    # scripts (cron), never an implicit side effect of a request. See docs/RETENCION.md.
    NOTIFICATION_RETENTION_DAYS: int = Field(default=90, ge=0)
    AUDIT_RETENTION_DAYS: int = Field(default=730, ge=0)
    # Never archive-and-delete audit rows younger than this, whatever the operator asks.
    AUDIT_RETENTION_FLOOR_DAYS: int = Field(default=90, ge=1)
    AUDIT_ARCHIVE_DIR: str = "audit-archive"
    AUDIT_EXPORT_MAX_ROWS: int = Field(default=50_000, ge=1)
    # Personal data of passengers (name, document, nationality, passport reference) is
    # anonymized this many days after the operation's accounting date, once it is closed.
    PASSENGER_RETENTION_DAYS: int = Field(default=1095, ge=0)
    # Unfinished uploads (a reserved slot whose document never got linked) are dropped
    # after this many hours by the maintenance script.
    UPLOAD_INTENT_STALE_HOURS: int = Field(default=24, ge=1)
    # Privileged connection used ONLY by the maintenance scripts that delete audit rows.
    # It must be a different role from the runtime one (docker/postgres/audit_maintenance.sql).
    MAINTENANCE_DATABASE_URL: str | None = None
    # How long an entrypoint waits for another replica to finish migrating (app.scripts.migrate).
    MIGRATION_LOCK_TIMEOUT_SECONDS: int = Field(default=600, ge=1)

    # Day boundaries of date-only filters (audit from/to) are computed in this zone.
    BUSINESS_TIMEZONE: str = "America/Caracas"

    # Password for demo users loaded by the seed. In production it must be provided.
    SEED_USER_PASSWORD: str | None = None

    # Blackbox (document archive). "fake" is an in-process stand-in for tests and local work;
    # it is refused in production. The Maestro token only ever comes from the environment.
    BLACKBOX_MODE: str = "real"
    BLACKBOX_BASE_URL: str | None = None
    BLACKBOX_MAESTRO_TOKEN: str | None = None
    BLACKBOX_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0, le=120)
    BLACKBOX_RETRY_BASE_DELAY: float = Field(default=0.5, ge=0, le=10)

    # Receipt reader: "fake" (deterministic, local/test), "anthropic" or "off".
    RECEIPT_READER: str = "fake"
    ANTHROPIC_API_KEY: str | None = None
    RECEIPT_READER_MODEL: str = "claude-sonnet-5-5"
    RECEIPT_READER_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0, le=120)

    SENTRY_DSN: str | None = None
    SENTRY_ENVIRONMENT: str | None = None
    SENTRY_RELEASE: str | None = None

    @property
    def is_local(self) -> bool:
        return self.ENVIRONMENT.lower() in LOCAL_ENVIRONMENTS

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in {"prod", "production"}

    @property
    def docs_enabled(self) -> bool:
        if self.ENABLE_DOCS is not None:
            return self.ENABLE_DOCS
        return self.is_local

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def migration_database_url(self) -> str:
        return self.MIGRATION_DATABASE_URL or self.DATABASE_URL

    @property
    def maintenance_database_url(self) -> str:
        return self.MAINTENANCE_DATABASE_URL or self.migration_database_url

    @property
    def access_token_ttl_seconds(self) -> int:
        return self.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    @property
    def refresh_token_ttl_seconds(self) -> int:
        return self.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600

    def security_issues(self) -> list[str]:
        """Configuration that must never reach a server."""
        issues: list[str] = []
        if len(self.JWT_SECRET) < MIN_SECRET_LENGTH:
            issues.append(f"JWT_SECRET must have at least {MIN_SECRET_LENGTH} characters")
        elif self.JWT_SECRET.lower() in KNOWN_PLACEHOLDER_SECRETS:
            issues.append("JWT_SECRET is a placeholder value")
        if self.DEBUG:
            issues.append("DEBUG must be false")
        if "*" in self.cors_origins:
            issues.append("ALLOWED_ORIGINS must not contain '*'")
        if self.BLACKBOX_MODE.lower() not in {"real", "fake"}:
            issues.append("BLACKBOX_MODE must be 'real' or 'fake'")
        if self.is_production and self.BLACKBOX_MODE.lower() == "fake":
            issues.append("BLACKBOX_MODE=fake would drop every attachment in production")
        if self.RECEIPT_READER.lower() not in {"fake", "anthropic", "off"}:
            issues.append("RECEIPT_READER must be 'fake', 'anthropic' or 'off'")
        if any(
            o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o
            for o in self.cors_origins
        ):
            issues.append("ALLOWED_ORIGINS has a non-local http:// origin")
        return issues


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.JWT_SECRET and settings.is_local:
        settings.JWT_SECRET = secrets.token_urlsafe(48)
        logger.warning("JWT_SECRET not set: using an ephemeral secret (tokens die on restart)")
    issues = settings.security_issues()
    if issues and not settings.is_local:
        raise RuntimeError("Insecure configuration: " + "; ".join(issues))
    if "*" in settings.cors_origins:
        raise RuntimeError("ALLOWED_ORIGINS must not contain '*'")
    if settings.is_production and not settings.REDIS_URL:
        # Valid with a single process; with N replicas every limit is really `limit x N`.
        logger.warning("REDIS_URL not set: rate limits are per process, not shared")
    return settings
