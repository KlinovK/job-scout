from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="JOB_SEARCH_",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./job_search.db"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    http_connect_timeout: float = Field(default=5.0, gt=0)
    http_read_timeout: float = Field(default=20.0, gt=0)
    http_write_timeout: float = Field(default=10.0, gt=0)
    http_pool_timeout: float = Field(default=5.0, gt=0)
    http_max_connections: int = Field(default=20, gt=0)
    http_max_keepalive_connections: int = Field(default=10, ge=0)
    collection_concurrency: int = Field(default=8, gt=0, le=32)
    user_agent: str = Field(default="personal-job-search/0.5", min_length=1)
    external_discovery_max_age_hours: int = Field(default=72, gt=0, le=720)
    external_discovery_max_pages: int = Field(default=5, gt=0, le=20)
    hh_api_token: str | None = None

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("sqlite+aiosqlite:///"):
            raise ValueError("Job Search Core requires a sqlite+aiosqlite database URL")
        return value
