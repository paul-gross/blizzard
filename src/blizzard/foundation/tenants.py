"""The tenant vocabulary the hub and its wire share: a tenant's state, how far its deletion has got, and the shape a
tenant's name takes."""

from __future__ import annotations

from enum import StrEnum

#: The most characters a tenant's name holds (``blizzard-product:/plans/multi-tenancy/hub/spec/store.md`` §The tenant
#: record).
TENANT_NAME_MAX_LENGTH = 64
#: The shape a tenant's name takes, as the wire carries it: no leading or trailing whitespace; inner whitespace, line
#: breaks included, is kept. The pattern stays ECMAScript-compatible, since the OpenAPI carries it.
TENANT_NAME_PATTERN = r"^\S(?:[\s\S]*\S)?$"


class TenantState(StrEnum):
    """Whether a tenant is open, or closed with its deletion underway."""

    OPEN = "open"
    DELETING = "deleting"


class TenantDeletionState(StrEnum):
    """Where a tenant's deletion stands: ``deleting`` while rows remain to drain, ``deleted`` once none do."""

    DELETED = "deleted"
    DELETING = "deleting"


def is_tenant_name(name: str) -> bool:
    """Whether ``name`` has a tenant name's shape — 1 to :data:`TENANT_NAME_MAX_LENGTH` characters, no leading or
    trailing whitespace."""
    return bool(name) and len(name) <= TENANT_NAME_MAX_LENGTH and name == name.strip()
