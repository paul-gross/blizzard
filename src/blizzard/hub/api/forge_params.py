"""Resolve a forge path segment inside the caller's tenant."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import Depends

from blizzard.hub.api import problems_admin
from blizzard.hub.api.auth_session import RequestTenant
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.domain.config.forges import ConfiguredForge, IReadForgeRepository


@inject_sync
def path_forge(
    forge: str,
    _tenant: RequestTenant,
    forges: FromDishka[IReadForgeRepository],
) -> ConfiguredForge:
    found = forges.resolve(forge)
    if found is None:
        raise ProblemHTTPException(partial(problems_admin.forge_not_found_problem, f"unknown forge {forge!r}"))
    return found


PathForge = Annotated[ConfiguredForge, Depends(path_forge)]
