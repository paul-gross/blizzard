"""The hub's open tenants, held in memory so resolving a request's tenant issues no statement."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from blizzard.hub.tenancy import TenantId


class OpenTenants:
    """The open tenants by id, with their names — read once, on first use, and read again only
    when a lookup names a tenant the copy in memory does not hold (a tenant opened since).
    ``load`` reads every open tenant."""

    def __init__(self, load: Callable[[], Mapping[TenantId, str]]) -> None:
        self._load = load
        self._tenants: Mapping[TenantId, str] | None = None

    def name(self, tenant: TenantId) -> str | None:
        """``tenant``'s name, or ``None`` when no open tenant has that id."""
        known = self._held()
        if tenant in known:
            return known[tenant]
        return self.refresh().get(tenant)

    def only(self) -> TenantId | None:
        """The one open tenant, or ``None`` while the hub holds several."""
        known = self._held()
        return next(iter(known)) if len(known) == 1 else None

    def every(self) -> Mapping[TenantId, str]:
        """Every open tenant's name by id, as held in memory."""
        return self._held()

    def refresh(self) -> Mapping[TenantId, str]:
        """Read the open tenants again, and hold that copy from now on."""
        # One reference swap: a concurrent reader holds either the old copy or the new one, whole.
        loaded = dict(self._load())
        self._tenants = loaded
        return loaded

    def _held(self) -> Mapping[TenantId, str]:
        held = self._tenants
        return held if held is not None else self.refresh()
