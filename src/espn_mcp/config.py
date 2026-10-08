"""Configuration management for ESPN MCP server."""

from typing import Literal

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
    ALLOWED_HOSTS: str = Field(
        default="",
        description="Comma-separated hostname allowlist for SSRF defense",
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

    # Stateless Streamable HTTP (MCP Spec 2026-07-28 / SEP-1049)
    MCP_STATELESS_HTTP: bool = Field(
        default=False,
        description="Run Streamable HTTP in stateless mode (fresh session per request)",
    )
    MCP_JSON_RESPONSE: bool = Field(
        default=False,
        description="Return direct JSON responses instead of SSE text/event-stream over HTTP",
    )

    # FastMCP 4 Server Composition & Layering
    MCP_PROFILE: str = Field(
        default="full",
        description=(
            "Server profile: domain-mount profiles 'full', 'games', 'teams', 'news', "
            "'readonly', or a job-shaped allowlist profile 'gameday', 'betting', 'scouting', "
            "'season' (see profiles.PROFILES)"
        ),
    )
    MCP_ENABLE_TOOL_SEARCH: bool = Field(
        default=False,
        description=(
            "Opt-in Tool Search transform (search_tools + call_tool). "
            "Attached only when profile is 'full'."
        ),
    )
    MCP_TOOL_SEARCH_BACKEND: Literal["regex", "bm25"] = Field(
        default="regex",
        description="Tool Search backend: 'regex' (default) or 'bm25'",
    )
    MCP_ENABLE_CODE_MODE: bool = Field(
        default=False,
        description=(
            "Opt-in experimental Code Mode transform (search + execute). "
            "Attached only when profile is 'full'; mutually exclusive with Tool Search."
        ),
    )


settings = Settings()
