"""Run a built hub app over the store-free process, as an app whose store is not wired answers."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from fastapi import FastAPI

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.hub.delivery.action_registry import SHIPPED_ACTIONS
from blizzard.hub.process_root import open_store_free


@contextmanager
def store_free(app: FastAPI) -> Generator[None]:
    """``app`` serves through a store-free hub process, with no services wired, until the block exits."""
    services = app.state.services
    process = app.state.hub_process
    free = open_store_free(
        clock=SystemClock(),
        config=app.state.config,
        platform_tracing=DisabledPlatformTracing(),
        environ={},
        actions=SHIPPED_ACTIONS,
    )
    app.state.services = None
    app.state.hub_process = free
    try:
        yield
    finally:
        app.state.services = services
        app.state.hub_process = process
        free.close()
