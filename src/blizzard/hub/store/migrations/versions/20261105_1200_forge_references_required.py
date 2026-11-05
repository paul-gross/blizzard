"""forge references required — repositories and work sources keyed on their forge, the address columns gone.

Before anything is written, the upgrade is refused, naming each record by tenant and slug, for any repository or work
source whose ``forge_id`` is null, names no forge, or names another tenant's forge. Then each table, in a batch of its
own, drops its address columns — ``repositories.forge_api_url``, ``work_sources.api_base`` and ``web_base`` — tightens
``forge_id`` to NOT NULL under a foreign key onto ``forges.forge_id`` (``fk_repositories_forges``,
``fk_work_sources_forges``), and rekeys its unique constraint on the forge under the name it already had:
``uq_repositories_tenant_coordinate (tenant_id, forge_id, owner, repo)`` and
``uq_work_sources_tenant_provider_locator (tenant_id, provider, forge_id, locator)``. Each table's step inspects first
and skips what a stopped run already did.

The downgrade is refused, naming every pair by tenant and slug, while a tenant holds one ``(provider, locator)`` on two
forges. Then it adds the address columns back and fills them from each record's forge — ``forge_api_url`` and
``api_base`` from the forge's ``api_base``, ``web_base`` from the forge's own — tightens ``forge_api_url`` to NOT NULL,
restores both unique constraints on the addresses, drops the foreign keys, and makes ``forge_id`` nullable again.
Revision ID: 20261105_1200_forge_references_required
Revises: 20261105_0900_routine_slug
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_1200_forge_references_required"
down_revision: str | None = "20261105_0900_routine_slug"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_REPOSITORIES = "repositories"
_SOURCES = "work_sources"
_FORGE_ID = "forge_id"
_FORGES = "forges"


#: Each referring table: its address columns, its foreign key, its unique constraint's name, and that constraint's
#: columns after and before this revision.
_TABLES: dict[str, tuple[tuple[str, ...], str, str, tuple[str, ...], tuple[str, ...]]] = {
    _REPOSITORIES: (
        ("forge_api_url",),
        "fk_repositories_forges",
        "uq_repositories_tenant_coordinate",
        ("tenant_id", _FORGE_ID, "owner", "repo"),
        ("tenant_id", "forge_api_url", "owner", "repo"),
    ),
    _SOURCES: (
        ("api_base", "web_base"),
        "fk_work_sources_forges",
        "uq_work_sources_tenant_provider_locator",
        ("tenant_id", "provider", _FORGE_ID, "locator"),
        ("tenant_id", "provider", "locator"),
    ),
}

# The tables this revision reads and fills, frozen to the columns it names (``bzh:frozen-revisions``).
_tenants = sa.table("tenants", sa.column("tenant_id", sa.String), sa.column("name", sa.String))
_forges = sa.table(
    _FORGES,
    sa.column(_FORGE_ID, sa.String),
    sa.column("tenant_id", sa.String),
    sa.column("api_base", sa.String),
    sa.column("web_base", sa.String),
)
_repositories = sa.table(
    _REPOSITORIES,
    sa.column("repository_id", sa.String),
    sa.column("tenant_id", sa.String),
    sa.column("slug", sa.String),
    sa.column(_FORGE_ID, sa.String),
    sa.column("forge_api_url", sa.String),
)
_sources = sa.table(
    _SOURCES,
    sa.column("work_source_id", sa.String),
    sa.column("tenant_id", sa.String),
    sa.column("slug", sa.String),
    sa.column("provider", sa.String),
    sa.column("locator", sa.String),
    sa.column(_FORGE_ID, sa.String),
    sa.column("api_base", sa.String),
    sa.column("web_base", sa.String),
)


def _columns(bind: sa.Connection, table: str) -> dict[str, bool]:
    """Each column of ``table`` mapped to whether it is nullable."""
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(bind).get_columns(table)}


def _uniques(bind: sa.Connection, table: str) -> dict[str, tuple[str, ...]]:
    return {str(u["name"]): tuple(u["column_names"]) for u in sa.inspect(bind).get_unique_constraints(table)}


def _foreign_keys(bind: sa.Connection, table: str) -> set[str]:
    return {str(f["name"]) for f in sa.inspect(bind).get_foreign_keys(table) if f.get("name")}


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps what its own reflection would drop."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def _tenant_names(bind: sa.Connection) -> dict[str, str]:
    return {str(row.tenant_id): str(row.name) for row in bind.execute(sa.select(_tenants.c.tenant_id, _tenants.c.name))}


# --- upgrade ---------------------------------------------------------------------------------------------------------


def _unreferenced(bind: sa.Connection) -> list[str]:
    """Each record whose ``forge_id`` this revision cannot key, as ``tenant …: <kind> <slug> <reason>``."""
    tenant_of = {
        str(row.forge_id): str(row.tenant_id)
        for row in bind.execute(sa.select(_forges.c.forge_id, _forges.c.tenant_id))
    }
    names = _tenant_names(bind)
    refused: list[str] = []
    for kind, table in (("repository", _repositories), ("work source", _sources)):
        rows = bind.execute(
            sa.select(table.c.tenant_id, table.c.slug, table.c.forge_id).order_by(table.c.tenant_id, table.c.slug)
        )
        for row in rows:
            tenant = f"tenant {names.get(row.tenant_id, row.tenant_id)} ({row.tenant_id})"
            if row.forge_id is None:
                reason = "names no forge"
            elif row.forge_id not in tenant_of:
                reason = f"names forge {row.forge_id}, which does not exist"
            elif tenant_of[row.forge_id] != row.tenant_id:
                reason = f"names forge {row.forge_id} of tenant {tenant_of[row.forge_id]}"
            else:
                continue
            refused.append(f"{tenant}: {kind} {row.slug} {reason}")
    return refused


def _refuse(bind: sa.Connection) -> None:
    """Refuse — before anything is written — every record naming no forge of its own tenant."""
    refused = _unreferenced(bind)
    if refused:
        raise RuntimeError(
            "these records name no forge of their own tenant, so their forge cannot be required: "
            + "; ".join(refused)
            + " — point each record named at a forge of its tenant, then upgrade again"
        )


def _contract(bind: sa.Connection, table: str) -> None:
    """One table: its address columns dropped, ``forge_id`` required under its foreign key, its unique rekeyed."""
    addresses, foreign_key, unique, keyed, _ = _TABLES[table]
    columns = _columns(bind, table)
    held = [column for column in addresses if column in columns]
    loose = columns[_FORGE_ID]
    unkeyed = foreign_key not in _foreign_keys(bind, table)
    rekey = _uniques(bind, table).get(unique) != keyed
    if not (held or loose or unkeyed or rekey):
        return
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        if rekey and unique in _uniques(bind, table):
            batch.drop_constraint(unique, type_="unique")
        for column in held:
            batch.drop_column(column)
        if loose:
            batch.alter_column(_FORGE_ID, existing_type=sa.String(), nullable=False)
        if unkeyed:
            batch.create_foreign_key(foreign_key, _FORGES, [_FORGE_ID], [_FORGE_ID])
        if rekey:
            batch.create_unique_constraint(unique, list(keyed))


def _contract_repositories(bind: sa.Connection) -> None:
    _contract(bind, _REPOSITORIES)


def _contract_sources(bind: sa.Connection) -> None:
    _contract(bind, _SOURCES)


#: The upgrade's steps, in order; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS: tuple[Callable[[sa.Connection], None], ...] = (_refuse, _contract_repositories, _contract_sources)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


# --- downgrade -------------------------------------------------------------------------------------------------------


def _locators_on_two_forges(bind: sa.Connection) -> list[str]:
    """Each tenant's ``(provider, locator)`` held on more than one forge, naming the work sources holding it."""
    s = _sources
    holders: defaultdict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in bind.execute(
        sa.select(s.c.tenant_id, s.c.provider, s.c.locator, s.c.slug, s.c.forge_id).order_by(
            s.c.tenant_id, s.c.provider, s.c.locator, s.c.slug
        )
    ):
        holders[(row.tenant_id, row.provider, row.locator)].append(f"{row.slug} (forge {row.forge_id})")
    names = _tenant_names(bind)
    return [
        f"tenant {names.get(tenant, tenant)} ({tenant}): {provider} {locator} is held by work sources {', '.join(slugs)}"
        for (tenant, provider, locator), slugs in holders.items()
        if len(slugs) > 1
    ]


def _refuse_down(bind: sa.Connection) -> None:
    """Refuse — before anything is written — a tenant holding one locator on two forges, which the restored unique
    constraint would refuse."""
    if "api_base" in _columns(bind, _SOURCES):
        return
    refused = _locators_on_two_forges(bind)
    if refused:
        raise RuntimeError(
            "the work sources' locators cannot be keyed without their forge again: "
            + "; ".join(refused)
            + " — change or delete all but one work source of each locator named, then downgrade again"
        )


def _add_addresses(bind: sa.Connection) -> None:
    for table, (addresses, *_rest) in _TABLES.items():
        columns = _columns(bind, table)
        for column in addresses:
            if column not in columns:
                op.add_column(table, sa.Column(column, sa.String(), nullable=True))


def _fill(bind: sa.Connection, table: sa.TableClause, key: str, check: str, values: dict[str, str]) -> None:
    """Every row whose ``check`` column is still null takes its forge's addresses, in bounded pages along its key."""
    forge = _forges.c.forge_id == table.c.forge_id
    filled = {column: sa.select(_forges.c[source]).where(forge).scalar_subquery() for column, source in values.items()}
    page = sa.select(table.c[key]).where(table.c[check].is_(None)).order_by(table.c[key]).limit(_BATCH_ROWS)
    while keys := list(bind.execute(page).scalars()):
        bind.execute(sa.update(table).where(table.c[key].in_(keys)).values(filled))


def _restore(bind: sa.Connection, table: str) -> None:
    addresses, foreign_key, unique, _, former = _TABLES[table]
    columns = _columns(bind, table)
    # Only the repository's address was required before this revision.
    tighten = table == _REPOSITORIES and columns[addresses[0]]
    loosen = not columns[_FORGE_ID]
    keyed = foreign_key in _foreign_keys(bind, table)
    rekey = _uniques(bind, table).get(unique) != former
    if not (tighten or loosen or keyed or rekey):
        return
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        if rekey and unique in _uniques(bind, table):
            batch.drop_constraint(unique, type_="unique")
        if keyed:
            batch.drop_constraint(foreign_key, type_="foreignkey")
        if loosen:
            batch.alter_column(_FORGE_ID, existing_type=sa.String(), nullable=True)
        if tighten:
            batch.alter_column(addresses[0], existing_type=sa.String(), nullable=False)
        if rekey:
            batch.create_unique_constraint(unique, list(former))


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_down(bind)
    _add_addresses(bind)
    _fill(bind, _repositories, "repository_id", "forge_api_url", {"forge_api_url": "api_base"})
    _fill(bind, _sources, "work_source_id", "api_base", {"api_base": "api_base", "web_base": "web_base"})
    _restore(bind, _REPOSITORIES)
    _restore(bind, _SOURCES)
