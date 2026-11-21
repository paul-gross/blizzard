"""Platform tracing's wiring: the tracer and client hook resolve to the borrowed handle's own members (unit tier)."""

from __future__ import annotations

import httpx2

from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing, IInstrumentClient, IPlatformTracing
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
from blizzard.foundation.platform_tracing.wiring import register
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import Kind, RecordingRegistry
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, build_container
from blizzard.foundation.registry.scopes import ScopeValues, supply


def test_the_tracer_and_hook_resolve_to_the_borrowed_handles_own() -> None:
    registry = RecordingRegistry()
    register(registry)
    handle = DisabledPlatformTracing()
    process = build_container(
        registry, HUB_LIFETIMES, owner="hub", values=ScopeValues((supply(port(IPlatformTracing), handle),))
    )
    assert process.resolver.get(IPlatformTracer) is handle.tracer
    hook = process.resolver.get(IInstrumentClient)
    assert hook == handle.instrument_client
    hook(httpx2.Client())
    assert process.resolver.get(IPlatformTracer) is process.resolver.get(IPlatformTracer)
    process.close()


def test_the_handle_is_borrowed_and_the_members_own_nothing() -> None:
    registry = RecordingRegistry()
    register(registry)
    kinds = {entry.key: (entry.kind, entry.lifetime) for entry in registry.entries}
    assert kinds[IPlatformTracing] == (Kind.CONTEXT, Lifetime.PROCESS)
    assert kinds[IPlatformTracer] == (Kind.FACTORY, Lifetime.PROCESS)
    assert kinds[IInstrumentClient] == (Kind.FACTORY, Lifetime.PROCESS)
