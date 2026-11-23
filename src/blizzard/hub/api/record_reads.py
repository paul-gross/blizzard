"""The reads a configured record's summary is shown through: the forges and credentials it names, and the users who
wrote it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import Depends

from blizzard.hub.api.auth_session import RequestTenant
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository


@dataclass(frozen=True)
class RecordReads:
    """The tenant's forge and credential records, and the user reads, a record's summary names."""

    forges: IReadForgeRepository
    credentials: IReadCredentialRepository
    users: IReadUserRepository


@inject_sync
def record_reads(
    _tenant: RequestTenant,
    forges: FromDishka[IReadForgeRepository],
    credentials: FromDishka[IReadCredentialRepository],
    users: FromDishka[IReadUserRepository],
) -> RecordReads:
    """The request's :class:`RecordReads`, each resolved through its scope."""
    return RecordReads(forges=forges, credentials=credentials, users=users)


Reads = Annotated[RecordReads, Depends(record_reads)]
