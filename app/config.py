from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.example", extra="ignore")
    app_env: Literal["production", "development", "test"] = "production"
    database_url: SecretStr = SecretStr("")
    public_origin: str = "https://localhost"
    cookie_secure: bool = True
    session_hours: int = Field(default=12, ge=1, le=168)
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = Field(default="gpt-4.1-mini", min_length=1, max_length=200)
    ai_timeout_seconds: float = Field(default=45, ge=1, le=180)
    ai_max_completion_tokens: int = Field(default=1800, ge=128, le=8000)
    ai_max_concurrent_requests: int = Field(default=4, ge=1, le=32)
    requests_per_minute: int = Field(default=10, ge=1, le=1000)
    requests_per_day: int = Field(default=200, ge=1, le=100000)
    login_attempts_per_minute: int = Field(default=20, ge=1, le=1000)
    max_pending_requests: int = Field(default=100, ge=1, le=10000)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    context_character_budget: int = Field(default=24000, ge=24000, le=100000)

    @field_validator("openai_base_url", "public_origin")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment
                or any(char.isspace() for char in value)):
            raise ValueError("Expected an http(s) URL without credentials, query or whitespace")
        _ = parts.port
        return value.rstrip("/")

    def validate_runtime(self, *, worker: bool = False) -> None:
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg://; no file storage fallback")
        if urlsplit(self.public_origin).path:
            raise ValueError("PUBLIC_ORIGIN must contain only scheme, host and optional port")
        if self.app_env == "production":
            if not self.cookie_secure or not self.public_origin.startswith("https://"):
                raise ValueError("Production requires HTTPS PUBLIC_ORIGIN and COOKIE_SECURE=true")
            if not self.openai_api_key.get_secret_value().strip():
                raise ValueError("Production requires OPENAI_API_KEY")
        if worker and not self.openai_api_key.get_secret_value().strip():
            raise ValueError("Worker requires OPENAI_API_KEY; demo responses are not supported")

    @property
    def session_cookie(self) -> str:
        return "__Host-vector-session" if self.cookie_secure else "vector-session"
