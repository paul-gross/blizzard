"""Run a built hub app over the store-free process, as an app whose store is not wired answers."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

from fastapi import FastAPI

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.hub.api.trace_continuation import TraceGatedFastAPI
from blizzard.hub.app import trace_principal_check
from blizzard.hub.delivery.action_registry import SHIPPED_ACTIONS
from blizzard.hub.process_root import open_store_free


@contextmanager
def store_free(app: FastAPI) -> Generator[None]:
    """``app`` serves through a store-free hub process until the block exits."""
    process = app.state.hub_process
    gate = cast(TraceGatedFastAPI, app)
    checker = gate.principal_check
    free = open_store_free(
        clock=SystemClock(),
        config=app.state.config,
        platform_tracing=DisabledPlatformTracing(),
        environ={},
        actions=SHIPPED_ACTIONS,
    )
    app.state.hub_process = free
    gate.principal_check = trace_principal_check(free, app.state.config)
    try:
        yield
    finally:
        app.state.hub_process = process
        gate.principal_check = checker
        free.close()
