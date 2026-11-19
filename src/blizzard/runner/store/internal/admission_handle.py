"""The concrete admission handle (package-private): the one write transaction an admission's runner and host halves
share."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy import Connection

from blizzard.runner.hosting.ports import IAdmissionHandle


@dataclass(frozen=True)
class AdmissionHandle:
    runner_id: str
    conn: Connection


def admission_of(handle: IAdmissionHandle) -> AdmissionHandle:
    """Recover the admission from a domain-held handle; only the runner admission builds one."""
    return cast(AdmissionHandle, handle)
