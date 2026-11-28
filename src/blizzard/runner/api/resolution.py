"""The request bridge between a host route and the host's container: a request on a host-root lane resolves its
services through the host's process scope (:func:`host_request`), borrowed and never closed here. A registration the
host-free graph declares absent answers ``503`` (:func:`refuse_absent_service`)."""

from __future__ import annotations

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler
from starlette.responses import Response

from blizzard.foundation.registry.interface import ServiceAbsent
from blizzard.runner.api.wiring import RunnerWiring, not_wired

#: The ``request.state`` attribute dishka's FastAPI integration resolves through.
_RESOLVER_STATE = "dishka_container"


async def host_request(request: Request) -> None:
    """A host-root lane dependency: the request resolves through the host's process scope."""
    setattr(request.state, _RESOLVER_STATE, RunnerWiring.of(request).host_resolver())


async def refuse_absent_service(request: Request, exc: Exception) -> Response:
    """Answer a resolution of a service the host-free graph lacks as that service not being wired."""
    assert isinstance(exc, ServiceAbsent)
    return await http_exception_handler(request, not_wired(exc.label))
