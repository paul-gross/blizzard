"""The hub's cross-tenant reads — the one seam a read that must span tenants goes through
(``bzh:hub-scoped-reads-confined``).

:class:`HubScopedReads` aggregates member groups, one per kind, and its membership is closed to
these kinds: resolving a credential to its tenant, resolving an invitation link's token to its
tenant, a sweep's corpus read, and tenant administration. Memberships and hub administrators are
global tables, read through their own repositories over :class:`HubStoreConnections` rather than
through this seam. A group reads over the hub-level :class:`HubStoreConnections` and never writes;
every statement it issues runs on a connection :func:`hub_scoped_read` marks with
:data:`HUB_SCOPED_OPTION`, which exempts it from the tenant statement guard. A group lives in a
module of its own beside this one, named ``hub_scoped_<kind>``, and the aggregate names it once;
such a group takes :func:`hub_scoped_read` already bound to the connections, so it never holds an
unmarked one. The composition root takes the seam whole through :func:`build_hub_scoped_reads`, so
no member group lands in a namespace another module imports."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial

from sqlalchemy import Connection, exists, select

from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.hub_scoped_corpus import SweepCorpusReads
from blizzard.hub.store.internal.hub_scoped_credentials import CredentialReads
from blizzard.hub.store.internal.hub_scoped_invitations import InvitationReads
from blizzard.hub.store.schema import tenant_deletions, tenants
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, TenantClosed, TenantId, UnknownTenant


@contextmanager
def hub_scoped_read(connections: HubStoreConnections, operation: str) -> Iterator[Connection]:
    """A read over ``connections`` whose every statement is marked hub-scoped."""
    with connections.read(operation) as conn:
        yield conn.execution_options(**{HUB_SCOPED_OPTION: True})


class TenantStateReads:
    """Tenant administration: whether a tenant exists and is still open."""

    def __init__(self, connections: HubStoreConnections) -> None:
        self._connections = connections

    def confirm_open(self, tenant: TenantId) -> None:
        """Raises :class:`UnknownTenant` when no tenant has this id, and :class:`TenantClosed`
        when it has a ``tenant_deletions`` row — whether its drain is still running or done."""
        with hub_scoped_read(self._connections, "read a tenant's state") as conn:
            known = conn.execute(select(tenants.c.tenant_id).where(tenants.c.tenant_id == tenant)).first()
            closed = conn.execute(
                select(tenant_deletions.c.id).where(tenant_deletions.c.tenant_id == tenant).limit(1)
            ).first()
        if closed is not None:
            raise TenantClosed(tenant)
        if known is None:
            raise UnknownTenant(tenant)

    def open_tenants(self) -> dict[TenantId, str]:
        """Every tenant with no ``tenant_deletions`` row, by id, with its name."""
        closed = exists().where(tenant_deletions.c.tenant_id == tenants.c.tenant_id)
        with hub_scoped_read(self._connections, "read the open tenants") as conn:
            rows = conn.execute(select(tenants.c.tenant_id, tenants.c.name).where(~closed)).all()
        return {TenantId(row.tenant_id): row.name for row in rows}


@dataclass(frozen=True)
class HubScopedReads:
    """Every read that spans tenants, one member group per kind."""

    tenants: TenantStateReads
    credentials: CredentialReads
    invitations: InvitationReads
    corpus: SweepCorpusReads


def build_hub_scoped_reads(connections: HubStoreConnections) -> HubScopedReads:
    """The seam over the hub-level ``connections``, every member group built."""
    return HubScopedReads(
        tenants=TenantStateReads(connections),
        credentials=CredentialReads(partial(hub_scoped_read, connections)),
        invitations=InvitationReads(partial(hub_scoped_read, connections)),
        corpus=SweepCorpusReads(partial(hub_scoped_read, connections)),
    )
