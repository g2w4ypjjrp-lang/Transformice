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

    @model_validator(mode="after")
    def _check_auth_config(self) -> "Settings":
        if self.environment in ("staging", "production"):
            if not self.supabase_jwt_secret and not self.supabase_url:
                raise ValueError("SUPABASE_JWT_SECRET or SUPABASE_URL (JWKS) must be configured")
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
