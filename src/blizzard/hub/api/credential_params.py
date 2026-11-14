"""Resolve a credential path segment inside the caller's tenant."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from fastapi import Depends

from blizzard.hub.api import problems_admin
from blizzard.hub.api.deps import Services
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.domain.config.credentials import ConfiguredCredential


def path_credential(credential: str, services: Services) -> ConfiguredCredential:
    found = services.credential_records.resolve(credential)
    if found is None:
        raise ProblemHTTPException(
            partial(problems_admin.credential_not_found_problem, f"unknown credential {credential!r}")
        )
    return found


PathCredential = Annotated[ConfiguredCredential, Depends(path_credential)]
