"""The hub's tenant containers: one locked child scope per open tenant, opened on first use and kept until evicted.

A tenant scope opens beneath the process scope this cache borrows, with the tenant's ``StoreScope`` as its context.
The cache owns the tenant scopes and nothing above them: ``close`` finalizes every tenant's resources and leaves the
process scope, and the engine, connections, tracing and settings it borrowed, to the entrypoint that built them.

A tenant scope that is closing or closed answers ``TenantClosed`` from every lookup, so a request racing a deletion or
the hub's shutdown is refused as a closed tenant, never as a server fault."""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from blizzard.foundation.registry.interface import Lifetime, ScopeClosed, port
from blizzard.foundation.registry.scopes import OwnedChild, Resolver, ScopeValues, open_child, supply
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId, UnknownTenant


class TenantContainers:
    """One tenant scope per open tenant, built under one creation lock so racing opens yield one scope."""

    def __init__(
        self,
        process: OwnedChild,
        *,
        confirm_open: Callable[[TenantId], None],
        open_tenants: Callable[[], Iterable[TenantId]],
        eager: Sequence[type[Any]] = (),
    ) -> None:
        """``process`` is the borrowed process scope; ``confirm_open`` raises ``UnknownTenant`` or ``TenantClosed``
        for a tenant that is not open; ``open_tenants`` names the tenants ``every_open`` walks; ``eager`` are the
        types resolved when a tenant opens, before it is published, for state the legacy bridge needs at once."""
        self._process = process
        self._confirm_open = confirm_open
        self._open_tenants = open_tenants
        self._eager = tuple(eager)
        self._entries: dict[TenantId, _TenantScope] = {}
        self._closed = False
        self._lock = threading.Lock()

    def open(self, scope: StoreScope) -> OwnedChild:
        """``scope``'s tenant scope, opened on its first use. Raises ``UnknownTenant`` or ``TenantClosed`` for a tenant
        that cannot be opened, and ``TenantClosed`` after ``close``."""
        return self._entry(scope).child

    def resolve[T](self, scope: StoreScope, dependency_type: type[T]) -> T:
        """The tenant's one ``dependency_type``, refused as ``open`` refuses."""
        return self._entry(scope).child.resolver.get(dependency_type)

    def every_open[T](self, dependency_type: type[T]) -> Iterator[tuple[TenantId, T]]:
        """Each open tenant's ``dependency_type`` in tenant-id order, skipping a tenant that is unknown or closed,
        including one closed while the walk runs."""
        for tenant in sorted(self._open_tenants()):
            try:
                resolved = self.resolve(StoreScope(tenant), dependency_type)
            except (UnknownTenant, TenantClosed):
                continue
            yield tenant, resolved

    def request(self, scope: StoreScope, values: ScopeValues) -> OwnedChild:
        """One request scope beneath ``scope``'s tenant scope, supplied with ``values``. Raises as ``open`` raises."""
        tenant = self._entry(scope).raw
        with _as_tenant_closed():
            return _TenantChild(open_child(tenant, lifetime=Lifetime.REQUEST, owner=scope.tenant, values=values))

    def evict(self, tenant: TenantId) -> None:
        """Close and forget ``tenant``'s scope, if open; the tenant's deletion calls this. Closes outside the lock."""
        with self._lock:
            entry = self._entries.pop(tenant, None)
        if entry is not None:
            entry.close()

    def close(self) -> None:
        """Refuse further opens, then close every tenant scope, attempting each and raising what failed together.

        The borrowed process scope stays open for its entrypoint to close afterwards."""
        with self._lock:
            self._closed = True
            entries = list(self._entries.values())
            self._entries.clear()
        failures: list[Exception] = []
        for entry in entries:
            try:
                entry.close()
            except Exception as failure:  # every tenant is attempted; each failure is reported
                failures.append(failure)
        if failures:
            raise ExceptionGroup("closing the tenant scopes", failures)

    def _entry(self, scope: StoreScope) -> _TenantScope:
        with self._lock:
            if self._closed:
                raise TenantClosed(scope.tenant)
            entry = self._entries.get(scope.tenant)
            if entry is None:
                entry = self._entries[scope.tenant] = self._open_new(scope)
        return entry

    def _open_new(self, scope: StoreScope) -> _TenantScope:
        self._confirm_open(scope.tenant)
        raw = open_child(
            self._process,
            lifetime=Lifetime.TENANT,
            owner=scope.tenant,
            values=ScopeValues((supply(port(StoreScope), scope),)),
        )
        child = _TenantChild(raw)
        try:
            for dependency_type in self._eager:
                child.resolver.get(dependency_type)
        except BaseException as failure:
            try:
                raw.close(failure)
            except Exception as cleanup:  # the initialization failure is the one reported
                failure.add_note(f"closing the tenant scope after it also failed: {cleanup!r}")
            raise
        return _TenantScope(child, raw)


class _TenantScope:
    """A tenant's open scope: ``raw`` is what children open beneath, ``child`` what callers resolve through."""

    def __init__(self, child: _TenantChild, raw: OwnedChild) -> None:
        self.child = child
        self.raw = raw

    def close(self) -> None:
        self.raw.close()


@contextmanager
def _as_tenant_closed() -> Generator[None]:
    """Answer a closed tenant scope as ``TenantClosed``; a closed scope of any other lifetime is not the tenant's."""
    try:
        yield
    except ScopeClosed as closed:
        if closed.lifetime is not Lifetime.TENANT:
            raise
        raise TenantClosed(TenantId(closed.owner)) from closed


class _TenantResolver:
    def __init__(self, resolver: Resolver) -> None:
        self._resolver = resolver

    def get(self, dependency_type: Any, component: str | None = "") -> Any:
        with _as_tenant_closed():
            return self._resolver.get(dependency_type, component)


class _TenantChild:
    """A scope at or beneath a tenant whose resolver answers a closed tenant as ``TenantClosed``."""

    def __init__(self, child: OwnedChild) -> None:
        self._child = child
        self.resolver: Resolver = _TenantResolver(child.resolver)

    @property
    def lifetime(self) -> Lifetime:
        return self._child.lifetime

    @property
    def owner(self) -> str:
        return self._child.owner

    def close(self, exception: BaseException | None = None) -> None:
        self._child.close(exception)
