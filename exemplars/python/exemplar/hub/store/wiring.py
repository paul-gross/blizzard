"""The permit adapter's registration, once at the tenant scope, its ``Ref`` handed back for the area to alias."""

# exemplar: bzh:internal-visibility — an adapter package's own wiring is the one module naming its ``internal/``.
# exemplar: production declares this beside the other adapters in ``blizzard.hub.store.wiring``.

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry
from exemplar.hub.store.internal.permit_store import PermitStore


def register_permits(registry: Registry) -> Ref[PermitStore]:
    """Declare the tenant's permit adapter."""
    return registry.service(PermitStore, lifetime=Lifetime.TENANT)
