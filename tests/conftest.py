"""Pytest fixtures and mocked HTTP transports."""

import httpx
import pytest


@pytest.fixture
def mock_transport():
    """Create a mock transport with pre-configured responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)

        if "health" in url_str:
            return httpx.Response(200, json={"status": "healthy", "uptime": 1234})
        if request.method == "GET" and "items/" in url_str:
            item_id = url_str.split("items/")[-1].split("?")[0]
            if item_id == "not-found":
                return httpx.Response(404, json={"error": "Not Found"})
            return httpx.Response(200, json={"id": item_id, "name": f"Item {item_id}"})
        if request.method == "GET" and "items" in url_str:
            return httpx.Response(200, json={"items": [{"id": "1", "name": "Item 1"}]})
        if request.method == "POST" and "items" in url_str:
            return httpx.Response(201, json={"id": "new-1", "created": True})
        if request.method == "DELETE" and "items/" in url_str:
            return httpx.Response(200, json={"deleted": True})
        if "auth-fail" in url_str:
            return httpx.Response(401, text="Unauthorized")
        if "server-error" in url_str:
            return httpx.Response(500, text="Internal Server Error")
        if "empty" in url_str:
            return httpx.Response(204)

        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)
