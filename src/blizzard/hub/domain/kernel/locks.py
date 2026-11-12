"""The hub-level locks: concepts no tenant owns, each a seeded row of the ``hub_locks`` table."""

from enum import StrEnum


class HubLock(StrEnum):
    """A lock on a concept no tenant owns. Taken in the order ``IDENTITY``, a tenant's own rows, ``TENANT_LIFECYCLE``
    (``bzh:store-exclusive-write``)."""

    IDENTITY = "identity"
    TENANT_LIFECYCLE = "tenant_lifecycle"
