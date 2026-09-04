"""Configuration management for ESPN MCP server."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable bindings."""

    model_config = SettingsConfigDict(
        env_prefix="ESPN_",
        env_file=".env",
        extra="ignore",
    )

    BASE_URL: str = Field(
        default="https://site.web.api.espn.com",
        description="Target ESPN Web API base URL",
    )
    CORE_BASE_URL: str = Field(
        default="https://sports.core.api.espn.com",
        description="Target ESPN Core API base URL",
    )
    TIMEOUT_SECONDS: float = Field(
        default=30.0,
        gt=0,
        description="HTTP request timeout in seconds",
    )
    MAX_RETRIES: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts on HTTP 429 / 5xx",
    )

    # Caching TTL configurations (SEP-2549)
    SCOREBOARD_CACHE_TTL_MS: int = Field(
        default=30000,
        gt=0,
        description="Cache TTL in milliseconds for live scoreboards",
    )
    CATALOG_CACHE_TTL_MS: int = Field(
        default=3600000,
        gt=0,
        description="Cache TTL in milliseconds for catalog discovery (tools/list, etc.)",
    )

    # Safety Gating
    MCP_READONLY: bool = Field(
        default=False,
        description="Restrict server strictly to tools marked readOnlyHint=True",
    )


settings = Settings()
