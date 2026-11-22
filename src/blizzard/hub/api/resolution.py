"""The request bridge between a route and the hub's container: which resolver a request resolves its services through.

Every route borrows the process scope's resolver first (:func:`process_request`); a tenant route's admitted request then
replaces it with a request scope opened beneath its tenant's scope (:func:`request_scope`), closed when the request
completes or is cancelled — never closing the tenant. A registration the store-free graph declares absent answers the
same ``503`` a store-backed route without services answers (:func:`refuse_absent_service`)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import Request
from starlette.responses import Response

from blizzard.foundation.registry.interface import ServiceAbsent
from blizzard.foundation.registry.scopes import Resolver, ScopeValues
from blizzard.hub.api import problems
from blizzard.hub.api.problem_exceptions import ProblemHTTPException, problem_http_exception
from blizzard.hub.tenancy import StoreScope

if TYPE_CHECKING:
    from blizzard.hub.process_root import HubProcess

#: The ``request.state`` attribute dishka's FastAPI integration resolves through.
_RESOLVER_STATE = "dishka_container"


def hub_process(request: Request) -> HubProcess:
    """The hub process the app was built over."""
    return request.app.state.hub_process


async def process_request(request: Request) -> None:
    """An app-level dependency: the request resolves through the process scope, borrowed and never closed here."""
    setattr(request.state, _RESOLVER_STATE, hub_process(request).resolver)


def request_resolver(request: Request) -> Resolver:
    """The resolver ``request`` currently resolves through: the process scope's, or its admitted request scope's."""
    return getattr(request.state, _RESOLVER_STATE)


@contextmanager
def request_scope(request: Request, scope: StoreScope) -> Generator[None]:
    """Resolve ``request`` through a request scope beneath ``scope``'s tenant scope until the block exits, then close
    that request scope alone. Raises ``UnknownTenant`` or ``TenantClosed`` for a tenant that cannot be opened."""
    child = hub_process(request).tenants.request(scope, ScopeValues())
    setattr(request.state, _RESOLVER_STATE, child.resolver)
    try:
        yield
    finally:
        child.close()


async def refuse_absent_service(request: Request, exc: Exception) -> Response:
    """Answer a resolution of a service the store-free graph lacks as the store's unavailability."""
    assert isinstance(exc, ServiceAbsent)
    return await problem_http_exception(request, ProblemHTTPException(problems.hub_store_unavailable_problem))
