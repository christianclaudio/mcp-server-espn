"""Tests for async HTTP client functionality."""

import httpx
import pytest

from template_mcp.client import TemplateClient
from template_mcp.errors import (
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    TemplateError,
)


@pytest.mark.asyncio
async def test_client_request_success(mock_transport):
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    client = TemplateClient(api_key="test-key", http_client=async_client)

    res = await client.request("GET", "health")
    assert res["status"] == "healthy"

    empty_res = await client.request("GET", "empty")
    assert empty_res == {}

    await client.close()


@pytest.mark.asyncio
async def test_client_path_sanitization():
    client = TemplateClient()
    sanitized = client.sanitize_path_param("../../../etc/passwd")
    assert ".." not in sanitized
    assert "%2E%2E" in sanitized or "%2F" in sanitized


@pytest.mark.asyncio
async def test_client_errors():
    def error_transport(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "401" in url:
            return httpx.Response(401, text="Unauthorized")
        if "404" in url:
            return httpx.Response(404, text="Not Found")
        if "429" in url:
            return httpx.Response(429, text="Rate Limited")
        if "500" in url:
            return httpx.Response(500, text="Server Error")
        return httpx.Response(200)

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://api.example.com"
    )
    client = TemplateClient(max_retries=1, http_client=async_client)

    with pytest.raises(AuthenticationError):
        await client.request("GET", "401")

    with pytest.raises(ResourceNotFoundError):
        await client.request("GET", "404")

    with pytest.raises(RateLimitError):
        await client.request("GET", "429")

    with pytest.raises(TemplateError):
        await client.request("GET", "500")


@pytest.mark.asyncio
async def test_client_network_error():
    def fail_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection failed")

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(fail_transport), base_url="https://api.example.com"
    )
    client = TemplateClient(max_retries=1, http_client=async_client)

    with pytest.raises(TemplateError) as exc_info:
        await client.request("GET", "network-fail")
    assert "Request failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_client_lifecycle():
    client = TemplateClient(api_key="key123")
    c = await client.get_client()
    assert c is not None
    assert "Bearer key123" in c.headers.get("Authorization", "")
    await client.close()
