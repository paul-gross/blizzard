"""SQLAlchemy adapter for the tenant's settings (package-private).

One ``tenant_settings`` row per tenant, keyed by it; every write commits its change row in the same transaction
(``bzh:configured-record``) and an edit is a compare-and-set on ``revision``."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.urgency.model import UrgencyId
from blizzard.hub.domain.urgency.ports import IWriteTenantSettings
from blizzard.hub.domain.urgency.settings import TenantSettings

# Appends the config audit record on the caller's connection, atomically with the write.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.schema import tenant_settings
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_in


def insert_settings(conn: Connection, settings: TenantSettings, change: ConfigChange) -> None:
    """The seed body, inside the caller's transaction: the row and its change."""
    conn.execute(
        statements_of(conn)
        .insert(tenant_settings)
        .values(
            default_urgency_id=settings.default_urgency_id,
            revision=settings.revision,
            updated_at=settings.updated_at,
            updated_by=settings.updated_by,
        )
    )
    append_change(conn, change)


def update_settings(conn: Connection, settings: TenantSettings, *, from_revision: int, change: ConfigChange) -> None:
    """The edit body, inside the caller's transaction: a compare-and-set on ``from_revision`` and the change;
    :class:`ConfigRevisionConflict` when the stored revision has moved."""
    stmts = statements_of(conn)
    moved = conn.execute(
        stmts.update(tenant_settings)
        .where(tenant_settings.c.revision == from_revision)
        .values(
            default_urgency_id=settings.default_urgency_id,
            revision=settings.revision,
            updated_at=settings.updated_at,
            updated_by=settings.updated_by,
        )
    ).rowcount
    if moved != 1:
        current = conn.execute(stmts.select(tenant_settings, tenant_settings.c.revision)).scalar_one()
        raise ConfigRevisionConflict("tenant_settings", settings.tenant_id, current=current)
    append_change(conn, change)


class TenantSettingsStore:
    """Read-write adapter over the tenant's settings."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def get(self, *, transaction: Transaction | None = None) -> TenantSettings | None:
        with connection_in(self._store, transaction, "get_tenant_settings") as conn:
            row = conn.execute(self._store.select(tenant_settings)).one_or_none()
        if row is None:
            return None
        return TenantSettings(
            tenant_id=row.tenant_id,
            default_urgency_id=UrgencyId(row.default_urgency_id),
            revision=row.revision,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )

    def create(
        self, settings: TenantSettings, *, change: ConfigChange, transaction: Transaction | None = None
    ) -> TenantSettings:
        with connection_in(self._store, transaction, "create_tenant_settings", write=True) as conn:
            insert_settings(conn, settings, change)
        return settings

    def update(
        self,
        settings: TenantSettings,
        *,
        from_revision: int,
        change: ConfigChange,
        transaction: Transaction | None = None,
    ) -> TenantSettings:
        with connection_in(self._store, transaction, "update_tenant_settings", write=True) as conn:
            update_settings(conn, settings, from_revision=from_revision, change=change)
        return settings


_tenant_settings_store_conforms: type[IWriteTenantSettings] = TenantSettingsStore
