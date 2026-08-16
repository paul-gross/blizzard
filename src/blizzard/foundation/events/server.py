"""The early-shutdown uvicorn server wrapper (D1, issue #47) — sets a shutdown signal
the instant SIGTERM/SIGINT is caught, ahead of uvicorn's own graceful drain, which an
SSE response never finishes on its own."""

from __future__ import annotations

import asyncio
from types import FrameType

import uvicorn


class EarlyShutdownServer(uvicorn.Server):
    """Sets ``shutdown_signal`` synchronously in ``handle_exit``, before uvicorn's
    graceful-drain wait an SSE response never finishes."""

    def __init__(self, config: uvicorn.Config, *, shutdown_signal: asyncio.Event) -> None:
        super().__init__(config)
        self._shutdown_signal = shutdown_signal

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._shutdown_signal.set()
        super().handle_exit(sig, frame)
