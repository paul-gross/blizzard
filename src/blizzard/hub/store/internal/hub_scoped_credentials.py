"""The credential member group of :class:`HubScopedReads` — the lookups a presented runner
credential resolves through before any tenant is known.

Each lookup reads one global-by-key row across every tenant and answers the row's tenant beside the
runner it names; it hydrates nothing. Everything the caller then reads about that runner — its
pauses, lifecycle, usage — it reads through the resolved tenant's own stores, never through here."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Row, select

from blizzard.foundation.roles import domain_model
from blizzard.hub.store.schema import host_token_revocations, hosts, runner_registrations, runner_token_revocations
from blizzard.hub.tenancy import TenantId

#: Opens a read, named by its operation, whose every statement is marked hub-scoped — the aggregate's
#: ``hub_scoped_read`` bound to the hub-level connections, so this group never holds an unmarked one.
MarkedRead = Callable[[str], AbstractContextManager[Connection]]


@domain_model
@dataclass(frozen=True)
class TenantRunner:
    """A runner a credential resolved to, and the tenant its row belongs to."""

    tenant: TenantId
    runner_id: str


@domain_model
@dataclass(frozen=True)
class TenantHost:
    """A host a credential resolved to, and the tenant its row belongs to."""

    tenant: TenantId
    host_id: str


class CredentialReads:
    """Resolving a runner's or a host's credential — its bearer token's hash, a revoked hash, or a
    runner's or a host's id at sign-in — to the runner or host and its tenant."""

    def __init__(self, read: MarkedRead) -> None:
        self._read = read

    def runner_for_token_hash(self, token_hash: str) -> TenantRunner | None:
        """The runner whose current token hashes to ``token_hash``, or ``None`` when none does."""
        with self._read("resolve a runner token") as conn:
            row = conn.execute(
                select(runner_registrations.c.tenant_id, runner_registrations.c.runner_id).where(
                    runner_registrations.c.token_hash == token_hash
                )
            ).one_or_none()
        return _tenant_runner(row)

    def revoked_token_runner(self, token_hash: str) -> TenantRunner | None:
        """The runner a revoked ``token_hash`` was issued to — the one its newest revocation names —
        or ``None`` when the hash was never revoked, whether revoked outright or rotated away from."""
        with self._read("resolve a revoked runner token") as conn:
            row = conn.execute(
                select(runner_token_revocations.c.tenant_id, runner_token_revocations.c.runner_id)
                .where(runner_token_revocations.c.token_hash == token_hash)
                .order_by(runner_token_revocations.c.id.desc())
                .limit(1)
            ).one_or_none()
        return _tenant_runner(row)

    def runner_by_id(self, runner_id: str) -> TenantRunner | None:
        """The runner ``runner_id`` names — a hub-minted id, so at most one tenant holds it — or
        ``None`` when no tenant does; how a sign-in naming a runner as its client finds the runner's
        tenant."""
        with self._read("resolve a runner id") as conn:
            row = conn.execute(
                select(runner_registrations.c.tenant_id, runner_registrations.c.runner_id).where(
                    runner_registrations.c.runner_id == runner_id
                )
            ).one_or_none()
        return _tenant_runner(row)

    def host_by_id(self, host_id: str) -> TenantHost | None:
        """The host ``host_id`` names — a hub-minted id, so at most one tenant holds it — or ``None`` when no
        tenant does; how a sign-in naming a host as its client finds the host's tenant."""
        with self._read("resolve a host id") as conn:
            row = conn.execute(
                select(hosts.c.tenant_id, hosts.c.host_id).where(hosts.c.host_id == host_id)
            ).one_or_none()
        return _tenant_host(row)

    def host_for_token_hash(self, token_hash: str) -> TenantHost | None:
        """The host whose current token hashes to ``token_hash``, or ``None`` when none does."""
        with self._read("resolve a host token") as conn:
            row = conn.execute(
                select(hosts.c.tenant_id, hosts.c.host_id).where(hosts.c.token_hash == token_hash)
            ).one_or_none()
        return _tenant_host(row)

    def revoked_token_host(self, token_hash: str) -> TenantHost | None:
        """The host a revoked ``token_hash`` was issued to — the one its newest revocation names — or
        ``None`` when the hash was never revoked, whether revoked outright or rotated away from."""
        with self._read("resolve a revoked host token") as conn:
            row = conn.execute(
                select(host_token_revocations.c.tenant_id, host_token_revocations.c.host_id)
                .where(host_token_revocations.c.token_hash == token_hash)
                .order_by(host_token_revocations.c.id.desc())
                .limit(1)
            ).one_or_none()
        return _tenant_host(row)


def _tenant_host(row: Row[Any] | None) -> TenantHost | None:
    # A row no scoped write stamped belongs to no tenant, so it resolves to nothing.
    if row is None or row.tenant_id is None:
        return None
    return TenantHost(tenant=TenantId(row.tenant_id), host_id=row.host_id)


def _tenant_runner(row: Row[Any] | None) -> TenantRunner | None:
    # A row no scoped write stamped belongs to no tenant, so it resolves to nothing.
    if row is None or row.tenant_id is None:
        return None
    return TenantRunner(tenant=TenantId(row.tenant_id), runner_id=row.runner_id)
