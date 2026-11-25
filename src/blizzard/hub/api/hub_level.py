"""The request bridge's hub-level dependency (``bzh:dependency-injection``).

``create_app`` stashes the request bridge's :class:`~blizzard.hub.composition.HubServices`, the opener of each
tenant's services, on ``app.state.services``; every hub-level owner is the process container's, which a route names
with ``FromDishka``. With no services wired, a route naming :data:`Hub` reports the store is unwired (503) rather than
serving on none."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from blizzard.hub.api import problems
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.composition import HubServices


def hub_services(request: Request) -> HubServices:
    """A dependency: the hub-level services, shared by every tenant."""
    services: HubServices | None = getattr(request.app.state, "services", None)
    if services is None:
        raise ProblemHTTPException(problems.hub_store_unavailable_problem)
    return services


Hub = Annotated[HubServices, Depends(hub_services)]
