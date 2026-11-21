"""The concrete document-apply handle (package-private): the one write transaction a host settings apply's members
share."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy import Connection

from blizzard.runner.settings.ports import IApplyHandle


@dataclass(frozen=True)
class ApplyHandle:
    conn: Connection


def apply_of(handle: IApplyHandle) -> ApplyHandle:
    """Recover the apply's transaction from a domain-held handle; only the host settings apply builds one."""
    return cast(ApplyHandle, handle)
