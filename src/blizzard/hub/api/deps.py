"""Shared FastAPI dependency for the fleet routers (``bzh:dependency-injection``).

The composition root stashes the wired :class:`~blizzard.hub.composition.HubServices`
on ``app.state.services``; routers reach it through this dependency. With no services
wired, a route reports the store is unwired (503) rather than serving on none."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import HTTPException

from blizzard.hub.composition import HubServices


def get_services(request: Request) -> HubServices:
    services: HubServices | None = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="hub store not wired — start via `blizzard hub host`",
        )
    return services
