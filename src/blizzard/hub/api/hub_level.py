"""The hub-level services dependency (``bzh:dependency-injection``).

The composition root stashes the wired hub-level :class:`~blizzard.hub.composition.HubServices` on
``app.state.services``; a route serving no tenant — identity, sessions, signing, exports — names
:data:`Hub` for it. With no services wired, a route reports the store is unwired (503) rather than
serving on none."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from blizzard.hub.composition import HubServices


def hub_services(request: Request) -> HubServices:
    """A dependency: the hub-level services, shared by every tenant."""
    services: HubServices | None = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="hub store not wired — start via `blizzard hub host`",
        )
    return services


Hub = Annotated[HubServices, Depends(hub_services)]
