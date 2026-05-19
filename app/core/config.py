from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    APP_NAME: str = "financial-intelligence-platform"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Literal["dev", "staging", "prod"] = "dev"
    DEBUG: bool = False

    # Database — use postgresql+asyncpg:// for the app; Alembic uses sync_database_url
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/finplatform"

    # Security
    SECRET_KEY: str = "change-me-in-production-use-at-least-32-chars"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALGORITHM: str = "HS256"

    # External APIs
    ALPHA_VANTAGE_KEY: str = ""

    # CORS
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    @property
    def sync_database_url(self) -> str:
        """psycopg2-compatible URL for Alembic migrations."""
        url = self.DATABASE_URL
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "+psycopg2")
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "prod"


settings = Settings()
