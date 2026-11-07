"""Resolve a forge path segment inside the caller's tenant."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from fastapi import Depends

from blizzard.hub.api import problems_admin
from blizzard.hub.api.deps import Services
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.domain.config.forges import ConfiguredForge


def path_forge(forge: str, services: Services) -> ConfiguredForge:
    found = services.forge_records.resolve(forge)
    if found is None:
        raise ProblemHTTPException(partial(problems_admin.forge_not_found_problem, f"unknown forge {forge!r}"))
    return found


PathForge = Annotated[ConfiguredForge, Depends(path_forge)]
