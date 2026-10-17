"""SQLAlchemy adapter for the host-identity repository seam (package-private).

Host-scoped: ``host_identity`` is a fact about this host, never a runner's, so its statements carry no runner
equality."""

from __future__ import annotations

from sqlalchemy import Connection, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityAlreadyRecorded, IWriteHostIdentityRepository
from blizzard.runner.store.schema import host_identity
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")

_COLUMNS = (host_identity.c.host_id, host_identity.c.tenant_id, host_identity.c.registered_at)


class HostIdentityStore:
    """Read-write adapter over the host store's single identity row."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def host_identity(self) -> HostIdentity | None:
        with self._store.connect() as conn:
            return _held(conn)

    def record_host_identity(self, identity: HostIdentity) -> None:
        with self._store.begin() as conn:
            held = _held(conn)
            if held is not None:
                if held.same_host(identity.host_id, identity.tenant_id):
                    return
                raise HostIdentityAlreadyRecorded(held, identity)
            conn.execute(
                host_identity.insert().values(
                    host_id=identity.host_id, tenant_id=identity.tenant_id, registered_at=identity.registered_at
                )
            )
        _log.info("host identity recorded", host_id=identity.host_id, tenant_id=identity.tenant_id)


def _held(conn: Connection) -> HostIdentity | None:
    row = conn.execute(select(*_COLUMNS).order_by(host_identity.c.id).limit(1)).first()
    if row is None:
        return None
    return HostIdentity(host_id=str(row.host_id), tenant_id=str(row.tenant_id), registered_at=row.registered_at)


def _conforms_host_identity_store(x: HostIdentityStore) -> IWriteHostIdentityRepository:
    return x
