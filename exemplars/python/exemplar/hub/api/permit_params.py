"""The permit a ``{permit_id}`` path segment names, loaded within the call's tenant or answered ``404``."""

# exemplar: bzh:domain-takes-objects — the edge resolves an id to its object before any service sees it.

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from exemplar.hub.api.deps import Services
from exemplar.hub.api.refusals import not_found
from exemplar.hub.domain.permits.model import Permit


def path_permit(permit_id: str, services: Services) -> Permit:
    """The permit ``{permit_id}`` names; ``404`` when the tenant holds none by that id."""
    found = services.permits.get(permit_id)
    if found is None:
        raise not_found(f"unknown permit {permit_id!r}")
    return found


PathPermit = Annotated[Permit, Depends(path_permit)]
