"""The tenant boundary's domain primitives — what names a tenant, and what a store scope is.

A tenant is named by its id alone (``ten_<ulid>``); its display name never stands in for it.
Nothing here touches the store: the seam that applies a scope lives in ``hub/store``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from blizzard.foundation.roles import domain_model

TenantId = NewType("TenantId", str)

# The SQLAlchemy execution-option key (value ``True``) that marks a statement as deliberately
# cross-tenant, exempting it from the tenant statement guard.
HUB_SCOPED_OPTION = "blizzard_hub_scoped"


@domain_model
@dataclass(frozen=True)
class StoreScope:
    """The tenant every read and write through a scoped store is confined to."""

    tenant: TenantId


class UnknownTenant(LookupError):
    """Raised when a tenant id names no tenant in the store."""

    def __init__(self, tenant: TenantId) -> None:
        super().__init__(f"unknown tenant {tenant}")
        self.tenant = tenant


class TenantClosed(Exception):
    """Raised when a write targets a tenant that is being, or has been, deleted."""

    def __init__(self, tenant: TenantId) -> None:
        super().__init__(f"tenant {tenant} is closed")
        self.tenant = tenant
