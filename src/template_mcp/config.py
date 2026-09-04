"""Configuration management for template MCP server."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable bindings."""

    model_config = SettingsConfigDict(
        env_prefix="TEMPLATE_",
        env_file=".env",
        extra="ignore",
    )

    API_KEY: str = Field(default="", description="Service API token")
    BASE_URL: str = Field(default="https://api.example.com", description="Target REST API base URL")
    TIMEOUT_SECONDS: float = Field(default=30.0, description="HTTP request timeout in seconds")
    MAX_RETRIES: int = Field(default=3, description="Maximum retry attempts on HTTP 429 / 5xx")

    # Safety Gating
    MCP_READONLY: bool = Field(
        default=False,
        description="Restrict server strictly to tools marked readOnlyHint=True",
    )
    MCP_ALLOW_BULK_DESTRUCTIVE: bool = Field(
        default=False,
        description="Gate required to register and execute batch destructive mutations",
    )


settings = Settings()
