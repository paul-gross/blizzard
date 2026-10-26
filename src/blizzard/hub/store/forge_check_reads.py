"""The forge pre-flight check's read of a hub store's address columns.

Reads the tables as it finds them, through frozen column constructs rather than ``src/blizzard/hub/store/schema.py``,
so it answers alike on a store before the forge migration and after. One tenant per statement: the constructs are not
``schema.metadata`` tables, so the tenant guard does not walk them, and the scoping is the rule this module follows."""

from __future__ import annotations

import sqlalchemy as sa

from blizzard.hub.domain.config.forge_check import RepositoryAddress, TenantAddresses, WorkSourceAddress

_TENANTS = sa.table("tenants", sa.column("tenant_id"), sa.column("name"))
_REPOSITORIES = sa.table(
    "repositories",
    sa.column("tenant_id"),
    sa.column("slug"),
    sa.column("forge_api_url"),
    sa.column("owner"),
    sa.column("repo"),
)
_WORK_SOURCES = sa.table(
    "work_sources",
    sa.column("tenant_id"),
    sa.column("slug"),
    sa.column("provider"),
    sa.column("api_base"),
    sa.column("web_base"),
)
_COLUMNS = {
    "tenants": ("tenant_id", "name"),
    "repositories": ("tenant_id", "slug", "forge_api_url", "owner", "repo"),
    "work_sources": ("tenant_id", "slug", "provider", "api_base", "web_base"),
}
#: The table the forge migration creates before it rewrites any address.
_CARRIED_OVER_TABLE = "forges"
_GITHUB = "github"


class UnreadableStore(Exception):
    """The store lacks a table or column the check reads."""


def read_forge_addresses(conn: sa.Connection) -> list[TenantAddresses] | None:
    """Every tenant's address records, closed tenants and retired records included; ``None`` for a store the forge
    migration has already carried over."""
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if _CARRIED_OVER_TABLE in tables:
        return None
    for table, columns in _COLUMNS.items():
        if table not in tables:
            raise UnreadableStore(f"the store has no {table} table")
        missing = [column for column in columns if column not in {c["name"] for c in inspector.get_columns(table)}]
        if missing:
            raise UnreadableStore(f"the {table} table has no {', '.join(missing)} column")
    tenants = conn.execute(sa.select(_TENANTS.c.tenant_id, _TENANTS.c.name).order_by(_TENANTS.c.tenant_id)).all()
    return [_read_tenant(conn, tenant_id, name) for tenant_id, name in tenants]


def _read_tenant(conn: sa.Connection, tenant_id: str, name: str) -> TenantAddresses:
    repositories = conn.execute(
        sa.select(_REPOSITORIES.c.slug, _REPOSITORIES.c.forge_api_url, _REPOSITORIES.c.owner, _REPOSITORIES.c.repo)
        .where(_REPOSITORIES.c.tenant_id == tenant_id)
        .order_by(_REPOSITORIES.c.slug)
    ).all()
    work_sources = conn.execute(
        sa.select(_WORK_SOURCES.c.slug, _WORK_SOURCES.c.api_base, _WORK_SOURCES.c.web_base)
        .where(_WORK_SOURCES.c.tenant_id == tenant_id, _WORK_SOURCES.c.provider == _GITHUB)
        .order_by(_WORK_SOURCES.c.slug)
    ).all()
    return TenantAddresses(
        tenant_id=tenant_id,
        tenant_name=name,
        repositories=tuple(RepositoryAddress(*row) for row in repositories),
        work_sources=tuple(WorkSourceAddress(*row) for row in work_sources),
    )
