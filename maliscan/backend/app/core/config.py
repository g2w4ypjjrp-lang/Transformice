"""Application settings, loaded exclusively from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # PostgreSQL connection of the *owner* role. Per-request transactions drop to
    # the `authenticated` role so Row Level Security applies.
    database_url: SecretStr = SecretStr("postgresql+psycopg://postgres@127.0.0.1:5432/maliscan")
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # Supabase Auth. Either the legacy HS256 secret or JWKS (asymmetric keys) is used.
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_issuer: str | None = None  # defaults to {supabase_url}/auth/v1 when url is set

    # Public URL of the web app (invitation links, password reset redirects).
    public_app_url: str = "http://localhost:3000"
    # Public base URL of this API (used for local signed download URLs).
    public_api_url: str = "http://localhost:8000"

    # ---- File storage ---------------------------------------------------------
    storage_backend: Literal["local", "supabase"] = "local"
    local_storage_dir: str = "./var/storage"
    # HMAC key for local signed URLs (required in staging/production with local storage).
    signing_secret: SecretStr = SecretStr("dev-only-signing-secret-change-me-0123456789")
    supabase_service_role_key: SecretStr | None = None
    signed_url_ttl_seconds: int = 300
    max_upload_bytes: int = 15 * 1024 * 1024

    # ---- AI / OCR ---------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    # none | google_vision  (Claude reads images directly; an OCR pre-pass is optional)
    ocr_provider: Literal["none", "google_vision"] = "none"
    google_vision_api_key: SecretStr | None = None
    # Below this confidence a document is always flagged for careful review.
    review_confidence_threshold: float = 0.85
    # Free on-server OCR (Tesseract, Turkish) used when no ANTHROPIC_API_KEY is set.
    local_ocr_enabled: bool = True

    # ---- Worker -------------------------------------------------------------------
    worker_poll_seconds: float = 2.0
    worker_id: str = "worker-1"
    # Free hosting (single instance): run the job worker inside the API process
    # and apply migrations on startup.
    embedded_worker: bool = False
    run_migrations_on_startup: bool = False

    @model_validator(mode="after")
    def _check_auth_config(self) -> "Settings":
        if self.environment in ("staging", "production"):
            if not self.supabase_jwt_secret and not self.supabase_url:
                raise ValueError("SUPABASE_JWT_SECRET or SUPABASE_URL (JWKS) must be configured")
            if self.signing_secret.get_secret_value().startswith("dev-only"):
                raise ValueError("SIGNING_SECRET must be set in staging/production")
            if self.storage_backend == "supabase" and not (self.supabase_url and self.supabase_service_role_key):
                raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for supabase storage")
        return self

    @property
    def jwt_issuer(self) -> str | None:
        if self.supabase_jwt_issuer:
            return self.supabase_jwt_issuer
        if self.supabase_url:
            return self.supabase_url.rstrip("/") + "/auth/v1"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
