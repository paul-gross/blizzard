"""A daemon's server keeps an idle connection open longer than any client that pools connections to it reuses one
(component tier): a client never sends on a connection the daemon is closing."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx2
import pytest

from blizzard.cli.runtime import build_early_shutdown_server
from tests.support import free_port

pytestmark = pytest.mark.component

_REQUEST = b"GET / HTTP/1.1\r\nHost: daemon\r\n\r\n"


async def _answering(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        await receive()
        await send({"type": "lifespan.startup.complete"})
        await receive()
        await send({"type": "lifespan.shutdown.complete"})
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
    await send({"type": "http.response.body", "body": b"ok"})


@contextmanager
def _serving(port: int) -> Iterator[None]:
    server = build_early_shutdown_server(_answering, host="127.0.0.1", port=port, shutdown_signal=asyncio.Event())
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started:
        assert time.monotonic() < deadline, "the daemon never started serving"
        time.sleep(0.05)
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(10.0)


def _answer(conn: socket.socket) -> bytes:
    conn.sendall(_REQUEST)
    received = b""
    while not received.endswith(b"ok"):
        chunk = conn.recv(4096)
        if not chunk:
            break
        received += chunk
    return received


def test_a_connection_idle_past_a_pooled_clients_reuse_still_answers() -> None:
    idle = httpx2.Limits().keepalive_expiry or 0.0
    port = free_port()
    with _serving(port), socket.create_connection(("127.0.0.1", port), timeout=5.0) as conn:
        assert _answer(conn).startswith(b"HTTP/1.1 200")
        time.sleep(idle + 1.0)

        assert _answer(conn).startswith(b"HTTP/1.1 200")
