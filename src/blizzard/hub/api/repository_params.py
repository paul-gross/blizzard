"""The repository a call names.

A repository a call names — a path segment — is its id or its slug, resolved here to the repository within the call's
tenant; nothing past the edge holds a slug. A slug the repository left still resolves to it, served without a
redirect; a display name never resolves. An unresolved repository answers ``404``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from blizzard.hub.api.deps import Services
from blizzard.hub.domain.config.repositories import ConfiguredRepository


def path_repository(repository: str, services: Services) -> ConfiguredRepository:
    """The repository the ``{repository}`` path segment names, by id, slug or former slug; ``404`` when none."""
    found = services.repository_records.resolve(repository)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown repository {repository!r}")
    return found


PathRepository = Annotated[ConfiguredRepository, Depends(path_repository)]
