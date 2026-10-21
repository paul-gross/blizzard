"""The work source a call names.

A work source a call names — a path segment or ``?source=`` — is its id or its slug, resolved here to the source
within the call's tenant; nothing past the edge holds a slug. A slug the source left still resolves to it, served
without a redirect; a display name never resolves. ``hub`` always names the built-in source, which has no record. An
unresolved source answers ``404`` on a path and ``422`` in a query."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.deps import Services
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.work_sources import (
    BuiltInWorkSource,
    ConfiguredWorkSource,
    is_built_in,
    require_configurable,
)
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME

#: The validation ``type`` of a ``422`` naming a work source the tenant does not hold.
UNKNOWN_WORK_SOURCE = "unknown_work_source"


def _unknown(value: str) -> str:
    return f"unknown work source {value!r}"


def source_key(value: str, services: TenantServices) -> str | None:
    """The key ``value`` names — ``hub`` for the built-in source, else the id of the configured source it names by id,
    slug or former slug — or ``None`` when it names none."""
    if is_built_in(value):
        return RESERVED_HUB_SOURCE_NAME
    found = services.work_source_records.resolve(value)
    return None if found is None else found.work_source_id


def path_source_key(source: str, services: Services) -> str:
    """The key the ``{source}`` path segment names (:func:`source_key`); ``404`` when it names none."""
    key = source_key(source, services)
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_unknown(source))
    return key


PathSourceKey = Annotated[str, Depends(path_source_key)]


def resolved_work_source(value: str, services: TenantServices) -> ConfiguredWorkSource:
    """The configured source ``value`` names, by id, slug or former slug; ``404`` when it names none."""
    found = services.work_source_records.resolve(value)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_unknown(value))
    return found


def path_work_source(source: str, services: Services) -> ConfiguredWorkSource:
    """The configured source a write's ``{source}`` path segment names, by id, slug or former slug; the built-in
    ``hub``, which is never written, is refused ``409`` by :func:`refuse_built_in_work_source`, and ``404`` when it
    names none."""
    require_configurable(source)
    return resolved_work_source(source, services)


def refuse_built_in_work_source(request: Request, exc: Exception) -> JSONResponse:
    """Answer a write on the built-in ``hub`` work source: ``409``."""
    assert isinstance(exc, BuiltInWorkSource)
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


PathWorkSource = Annotated[ConfiguredWorkSource, Depends(path_work_source)]


def query_source(services: Services, source: str | None = None) -> str | None:
    """The key ``?source=`` narrows a read to — ``None`` when the query names none; ``422`` when it names a source the
    tenant does not hold."""
    if source is None:
        return None
    key = source_key(source, services)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["query", "source"], "msg": _unknown(source), "type": UNKNOWN_WORK_SOURCE}],
        )
    return key


QuerySource = Annotated[str | None, Depends(query_source)]
