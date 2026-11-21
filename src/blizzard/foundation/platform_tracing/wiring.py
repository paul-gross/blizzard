"""The process's platform tracing: the one borrowed handle, and the tracer and client hook that are its members.

The tracer and the hook are projections of the handle, not aliases of a registration: each is a factory that constructs
nothing and owns nothing, so the handle stays the single instance and its owner finalizes it."""

from __future__ import annotations

from blizzard.foundation.platform_tracing.handle import IInstrumentClient, IPlatformTracing
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
from blizzard.foundation.registry.interface import Lifetime, Registry, port


def register(registry: Registry) -> None:
    """Declare the borrowed handle and the tracer and client hook that resolve to its own."""
    registry.context(port(IPlatformTracing), lifetime=Lifetime.PROCESS)
    registry.factory(_tracer, lifetime=Lifetime.PROCESS)
    registry.factory(_instrument_client, lifetime=Lifetime.PROCESS)


def _tracer(tracing: IPlatformTracing) -> IPlatformTracer:
    return tracing.tracer


def _instrument_client(tracing: IPlatformTracing) -> IInstrumentClient:
    return tracing.instrument_client
