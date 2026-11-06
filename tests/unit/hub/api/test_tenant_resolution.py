"""The tenant a resolved request binds: it joins every log line and the request's span (unit tier)."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import Request
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard.foundation.logging import configure
from blizzard.foundation.platform_tracing.attributes import TENANT, TENANT_NAME
from blizzard.hub.api.auth_session import TenantRequest, implicit_operator, tenant_bound
from blizzard.hub.tenancy import StoreScope, TenantId


def test_the_resolved_tenant_joins_every_log_line_and_the_requests_span() -> None:
    configure()
    tenant = TenantId("ten_acme")
    resolved = TenantRequest(
        identity=implicit_operator(tenant),
        scope=StoreScope(tenant),
        name="acme",
        services=None,  # pyright: ignore[reportArgumentType]  # tenant_bound reads only scope and name; a real tenant would make this a component test
    )
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    async def request() -> dict[str, object]:
        await tenant_bound(Request({"type": "http"}), resolved)
        return structlog.contextvars.get_contextvars()

    with provider.get_tracer(__name__).start_as_current_span("request"):
        bound = asyncio.run(request())

    assert bound["tenant_id"] == tenant
    assert structlog.contextvars.merge_contextvars in structlog.get_config()["processors"]
    (span,) = exporter.get_finished_spans()
    assert span.attributes is not None
    assert (span.attributes[TENANT], span.attributes[TENANT_NAME]) == (tenant, "acme")
