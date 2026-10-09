"""The hub's open tenants, held in memory so resolving a request's tenant issues no statement."""

from __future__ import annotations

# The lock orders this process's loads of its in-memory copy; it guards no store decision
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Callable, Mapping

from blizzard.hub.tenancy import TenantId


class OpenTenants:
    """The open tenants by id, with their names — read once, on first use, and read again when a
    change to the tenant set commits or a lookup names a tenant the copy in memory does not hold.
    ``load`` reads every open tenant."""

    def __init__(self, load: Callable[[], Mapping[TenantId, str]]) -> None:
        self._load = load
        self._tenants: Mapping[TenantId, str] | None = None
        self._loading = threading.Lock()

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
        """Read the open tenants again, and hold that copy from now on. A refresh called after a change to the
        tenant set commits ends holding a copy read after that commit: no load that started earlier replaces it."""
        # Loads run one at a time, each held from its read to its swap, so the copy held is always the latest read.
        # The swap is one reference assignment: a reader outside the lock holds either the old copy or the new one.
        with self._loading:
            loaded = dict(self._load())
            self._tenants = loaded
            return loaded

    def _held(self) -> Mapping[TenantId, str]:
        held = self._tenants
        return held if held is not None else self.refresh()
