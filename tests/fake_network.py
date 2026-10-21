"""Offline stand-in for the socket layer under httpx, sync and async.

Patches ``connect_tcp`` on httpcore's public ``SyncBackend`` and ``AnyIOBackend``, so no
test opens a socket. A hostname is resolved the way the real backends resolve it, through
``socket.getaddrinfo``, so a second lookup shows up in the resolver's call count. Each
connect target, TLS server name and request is recorded per connection.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Collection
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

import httpcore

DEFAULT_BODY: Any = {"ok": True}


class FakeNetwork:
    """Answer every request with one canned HTTP/1.1 response, recording what was sent."""

    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        fail: Collection[str] = (),
    ) -> None:
        payload = json.dumps(DEFAULT_BODY if body is None else body).encode()
        reason = b"OK" if status == 200 else b"Error"
        self.response = (
            b"HTTP/1.1 %d %s\r\nContent-Type: application/json\r\n"
            b"Content-Length: %d\r\n\r\n" % (status, reason, len(payload))
        ) + payload
        # Addresses whose connect fails, as an unreachable host would.
        self.fail = set(fail)
        self.attempts: list[tuple[str, int]] = []
        self.connects: list[tuple[str, int]] = []
        self.tls_server_names: list[str | None] = []
        # Raw bytes written on each opened connection, in connect order.
        self.streams: list[bytearray] = []

    @property
    def sent(self) -> bytes:
        return b"".join(self.streams)

    def requests_by_connection(self) -> list[list[bytes]]:
        """The request line of every request, grouped by the connection that carried it."""
        return [
            [line for line in bytes(s).split(b"\r\n") if line.endswith(b" HTTP/1.1")]
            for s in self.streams
        ]

    def _connect(self, host: str, port: int) -> bytearray:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        self.attempts.append((host, port))
        if host in self.fail:
            raise httpcore.ConnectError(f"connect to {host} failed")
        self.connects.append((host, port))
        written = bytearray()
        self.streams.append(written)
        return written

    def _on_write(self, written: bytearray, buffer: bytes, pending: bytearray) -> None:
        written += buffer
        # One response per request line (a body arrives in later writes).
        if buffer.split(b"\r\n", 1)[0].endswith(b" HTTP/1.1"):
            pending += self.response

    def patches(self) -> ExitStack:
        net = self

        class SyncStream(httpcore.NetworkStream):
            def __init__(self, written: bytearray) -> None:
                self.written = written
                self.pending = bytearray()

            def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
                chunk = bytes(self.pending[:max_bytes])
                del self.pending[:max_bytes]
                return chunk

            def write(self, buffer: bytes, timeout: float | None = None) -> None:
                net._on_write(self.written, buffer, self.pending)

            def close(self) -> None:
                pass

            def start_tls(
                self,
                ssl_context: Any,
                server_hostname: str | None = None,
                timeout: float | None = None,
            ) -> httpcore.NetworkStream:
                net.tls_server_names.append(server_hostname)
                return self

        class AsyncStream(httpcore.AsyncNetworkStream):
            def __init__(self, written: bytearray) -> None:
                self.written = written
                self.pending = bytearray()

            async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
                chunk = bytes(self.pending[:max_bytes])
                del self.pending[:max_bytes]
                return chunk

            async def write(self, buffer: bytes, timeout: float | None = None) -> None:
                net._on_write(self.written, buffer, self.pending)

            async def aclose(self) -> None:
                pass

            async def start_tls(
                self,
                ssl_context: Any,
                server_hostname: str | None = None,
                timeout: float | None = None,
            ) -> httpcore.AsyncNetworkStream:
                net.tls_server_names.append(server_hostname)
                return self

        def sync_connect(_self: Any, host: str, port: int, **_kw: Any) -> SyncStream:
            return SyncStream(net._connect(host, port))

        async def async_connect(_self: Any, host: str, port: int, **_kw: Any) -> AsyncStream:
            return AsyncStream(net._connect(host, port))

        stack = ExitStack()
        stack.enter_context(patch.object(httpcore.SyncBackend, "connect_tcp", sync_connect))
        stack.enter_context(patch.object(httpcore.AnyIOBackend, "connect_tcp", async_connect))
        return stack


def answer(*ips: str, port: int = 443) -> list[tuple[Any, ...]]:
    """A ``socket.getaddrinfo`` result listing ``ips`` in order."""
    out: list[tuple[Any, ...]] = []
    for ip in ips:
        if ":" in ip:
            out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0)))
        else:
            out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
    return out
