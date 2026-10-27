"""Resolve a forge path segment inside the caller's tenant."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from blizzard.hub.api.deps import Services
from blizzard.hub.domain.config.forges import ConfiguredForge


def path_forge(forge: str, services: Services) -> ConfiguredForge:
    found = services.forge_records.resolve(forge)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown forge {forge!r}")
    return found


PathForge = Annotated[ConfiguredForge, Depends(path_forge)]
