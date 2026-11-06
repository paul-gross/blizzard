"""A bounded pass's share of each tenant's due items, taken in turn so one tenant's burst never changes another's
share (``bzh:pass-isolates-tenants``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from blizzard.hub.tenancy import TenantId


def fair_share[T](due: Mapping[TenantId, Sequence[T]], limit: int, start: int) -> dict[TenantId, list[T]]:
    """Up to ``limit`` of ``due``, one item per tenant in turn from the ``start``-th tenant by name."""
    tenants = sorted(due)
    if tenants:
        start %= len(tenants)
        tenants = tenants[start:] + tenants[:start]
    taken: dict[TenantId, list[T]] = {}
    depth = 0
    while sum(map(len, taken.values())) < limit:
        round_ = [tenant for tenant in tenants if len(due[tenant]) > depth]
        if not round_:
            break
        for tenant in round_[: limit - sum(map(len, taken.values()))]:
            taken.setdefault(tenant, []).append(due[tenant][depth])
        depth += 1
    return taken
