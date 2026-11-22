"""Resolve a credential path segment inside the caller's tenant."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import Depends

from blizzard.hub.api import problems_admin
from blizzard.hub.api.auth_session import AdmittedTenant, tenant_request
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.domain.config.credentials import ConfiguredCredential, IReadCredentialRepository


@inject_sync
def path_credential(
    credential: str,
    _tenant: Annotated[AdmittedTenant, Depends(tenant_request)],
    credentials: FromDishka[IReadCredentialRepository],
) -> ConfiguredCredential:
    found = credentials.resolve(credential)
    if found is None:
        raise ProblemHTTPException(
            partial(problems_admin.credential_not_found_problem, f"unknown credential {credential!r}")
        )
    return found


PathCredential = Annotated[ConfiguredCredential, Depends(path_credential)]
