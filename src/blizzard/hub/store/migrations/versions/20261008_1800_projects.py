"""projects — every tenant holds a ``default`` project, linked to its work sources and repositories, and every
project-owned row names it.

Six tables are created, each carrying ``tenant_id``: ``projects`` (hub-unique ``project_id``, a slug unique within the
tenant), ``project_former_slugs``, and for work sources and repositories alike a link table keyed by project and name
with an append-only fact table beside it. Every tenant, closed ones included, in id order, gains a project — a
``proj_`` ULID minted at the time this migration runs, slug ``default``, name ``Default``, no description, revision 1 —
and its ``create`` change row, attributed to ``migration``. Every work source and every repository is linked to the
tenant's project: a link at revision 1, a ``linked`` fact set by ``migration`` at the tenant's earliest
``chunks.minted_at`` (the time this migration runs for a tenant holding no chunk), and the link's ``create`` change row.
The built-in ``hub`` source has no work-source row and so no link.

``chunks``, ``scopes``, ``routines``, ``findings``, ``finding_sets``, ``garden_proposals``, ``work_item_runs`` and
``work_items`` gain a ``project_id``, added nullable, filled from the row's tenant's one project — paging along each
table's key in bounded batches — then made required with ``fk_<table>_projects``. ``config_changes`` gains a nullable
``project_id`` with its foreign key, filled only on a scope's or a routine's change rows. A scope's slug and a routine's
name become unique within the project instead of the tenant, a tenant-led index on each standing in for the old unique.
``chunks`` gains a project-led index beside its tenant one; the garden's routine and scope indexes gain project-led ones
beside them, the tenant-led ones staying for the reads that name no project; the change log gains a project index. The
upgrade refuses to finish, naming the tenants, if any tenant ends holding other than one project or any project-owned
row is left without one. Each step inspects first and skips what a stopped run already did: a tenant holding a project
is given no second one, a link, fact or change row already written is not written again.

The downgrade refuses while any tenant holds more than one project, naming the tenants — a slug and a routine name are
unique within the tenant again. Otherwise it restores the tenant-wide uniques, drops the indexes it added and every
``project_id``, deletes the change rows of projects and their links — the older hub cannot read those kinds — and drops
the six tables. A project's renames, its former slugs and the history of its links are lost.
Revision ID: 20261008_1800_projects
Revises: 20261008_1720_tenant_deletion_closed_at
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.migrations import migration_clock
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261008_1800_projects"
down_revision: str | None = "20261008_1720_tenant_deletion_closed_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_TENANT_COLUMN = "tenant_id"
_PROJECT_COLUMN = "project_id"
_MIGRATION = "migration"
_PROJECT_PREFIX = "proj"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26  # 48 bits of millisecond timestamp, then 80 random bits
_RANDOM_BITS = 80
_DEFAULT_SLUG = "default"
_DEFAULT_NAME = "Default"

_PROJECTS = "projects"
_FORMER_SLUGS = "project_former_slugs"
_SOURCE_LINKS = "project_source_links"
_SOURCE_LINK_FACTS = "project_source_link_facts"
_REPOSITORY_LINKS = "project_repository_links"
_REPOSITORY_LINK_FACTS = "project_repository_link_facts"
#: In the order they are created; dropped in reverse.
_NEW_TABLES = (_PROJECTS, _FORMER_SLUGS, _SOURCE_LINKS, _SOURCE_LINK_FACTS, _REPOSITORY_LINKS, _REPOSITORY_LINK_FACTS)

#: Each project-owned table and the key its fill pages along.
_OWNED: dict[str, str] = {
    "chunks": "chunk_id",
    "scopes": "scope_id",
    "routines": "routine_id",
    "findings": "finding_id",
    "finding_sets": "finding_set_id",
    "garden_proposals": "proposal_id",
    "work_item_runs": "work_item_id",
    "work_items": "work_item_id",
}
_CHANGES = "config_changes"
#: The change rows that name a record inside a project: a scope's slug, a routine's name.
_PROJECT_KINDS = ("scope", "routine")
#: The change-row kinds this revision introduces, which the older hub cannot read.
_NEW_KINDS = ("project", "project_source_link", "project_repository_link")

#: Each table's tenant-wide unique and the project-wide one replacing it, with its columns.
_UNIQUES: dict[str, tuple[str, str, tuple[str, str]]] = {
    "scopes": ("uq_scopes_tenant_slug", "uq_scopes_project_slug", ("project_id", "slug")),
    "routines": ("uq_routines_tenant_name", "uq_routines_project_name", ("project_id", "name")),
}

#: Indexes this revision adds: name, table, columns.
_ADDED_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_chunks_tenant_project_minted_at_chunk_id", "chunks", ("tenant_id", "project_id", "minted_at", "chunk_id")),
    ("ix_scopes_tenant_slug", "scopes", ("tenant_id", "slug")),
    ("ix_routines_tenant_name", "routines", ("tenant_id", "name")),
    ("ix_findings_tenant_project_routine_scope", "findings", ("tenant_id", "project_id", "routine_name", "scope_slug")),
    ("ix_findings_tenant_project_routine_class", "findings", ("tenant_id", "project_id", "routine_name", "class")),
    ("ix_findings_tenant_project_scope_source", "findings", ("tenant_id", "project_id", "scope_slug", "source")),
    (
        "ix_finding_sets_tenant_project_routine_scope",
        "finding_sets",
        ("tenant_id", "project_id", "routine_name", "scope_slug"),
    ),
    (
        "ix_garden_proposals_tenant_project_routine_class",
        "garden_proposals",
        ("tenant_id", "project_id", "routine_name", "class"),
    ),
    ("ix_config_changes_tenant_project_id", "config_changes", ("tenant_id", "project_id", "id")),
)

_tenants = sa.table("tenants", sa.column(_TENANT_COLUMN, sa.String))
_projects = sa.table(
    _PROJECTS,
    sa.column(_PROJECT_COLUMN, sa.String),
    sa.column(_TENANT_COLUMN, sa.String),
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("revision", sa.Integer),
    sa.column("created_at", UtcDateTime),
)
_changes = sa.table(
    _CHANGES,
    sa.column("id", sa.Integer),
    sa.column("recorded_at", UtcDateTime),
    sa.column("actor", sa.String),
    sa.column("door", sa.String),
    sa.column("record_kind", sa.String),
    sa.column("record_key", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("op", sa.String),
    sa.column("diff", sa.Text),
    sa.column("apply_id", sa.String),
    sa.column(_TENANT_COLUMN, sa.String),
    sa.column(_PROJECT_COLUMN, sa.String),
)
_chunks = sa.table("chunks", sa.column(_TENANT_COLUMN, sa.String), sa.column("minted_at", UtcDateTime))


def _link_tables(
    links: str, facts: str, records: str, name_column: str
) -> tuple[sa.TableClause, sa.TableClause, sa.TableClause]:
    return (
        sa.table(
            links,
            sa.column(_TENANT_COLUMN, sa.String),
            sa.column(_PROJECT_COLUMN, sa.String),
            sa.column(name_column, sa.String),
            sa.column("revision", sa.Integer),
        ),
        sa.table(
            facts,
            sa.column("id", sa.Integer),
            sa.column(_TENANT_COLUMN, sa.String),
            sa.column(_PROJECT_COLUMN, sa.String),
            sa.column(name_column, sa.String),
            sa.column("linked", sa.Boolean),
            sa.column("set_at", UtcDateTime),
            sa.column("set_by", sa.String),
        ),
        sa.table(records, sa.column(_TENANT_COLUMN, sa.String), sa.column("name", sa.String)),
    )


#: For work sources and repositories alike: the record table, the link's name column, its change-row kind, and the
#: link and fact tables.
_LINKS: tuple[tuple[str, str, str, str, str], ...] = (
    ("work_sources", "source_name", "project_source_link", _SOURCE_LINKS, _SOURCE_LINK_FACTS),
    ("repositories", "repository_name", "project_repository_link", _REPOSITORY_LINKS, _REPOSITORY_LINK_FACTS),
)


def _columns(bind: sa.Connection, table: str) -> dict[str, bool]:
    """Each column of ``table`` mapped to whether it is nullable."""
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(bind).get_columns(table)}


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(bind).get_indexes(table)}


def _unique_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(unique["name"]) for unique in sa.inspect(bind).get_unique_constraints(table)}


def _foreign_key_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(fk["name"]) for fk in sa.inspect(bind).get_foreign_keys(table) if fk.get("name")}


def _has_table(bind: sa.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a batch left behind when its run stopped part way.

    SQLite creates the copy outside the transaction the rest of the batch runs in, so a stopped run can keep it while
    the table itself is rolled back; the name is the one Alembic's batch gives it.
    """
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, the structure every copying batch starts from: a batch left to reflect for
    itself drops an unnamed ``CHECK`` constraint from the copy."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def _encode(value: int) -> str:
    chars = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _mint_project_id(at: datetime) -> str:
    millis = int(at.timestamp() * 1000)
    randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
    return f"{_PROJECT_PREFIX}_{_encode((millis << _RANDOM_BITS) | randomness)}"


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_TENANT_COLUMN], ["tenants.tenant_id"], name=f"fk_{table}_tenant_id")


def _project_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_PROJECT_COLUMN], ["projects.project_id"], name=f"fk_{table}_projects")


def _create_link_tables(bind: sa.Connection, records: str, name_column: str, links: str, facts: str) -> None:
    if not _has_table(bind, links):
        op.create_table(
            links,
            sa.Column(_PROJECT_COLUMN, sa.String(), nullable=False),
            sa.Column(name_column, sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_PROJECT_COLUMN, name_column, name=f"pk_{links}"),
            _project_fk(links),
            sa.ForeignKeyConstraint(
                [_TENANT_COLUMN, name_column], [f"{records}.tenant_id", f"{records}.name"], name=f"fk_{links}_{records}"
            ),
            _tenant_fk(links),
        )
    index = f"ix_{links}_tenant_{name_column.removesuffix('_name')}"
    if index not in _index_names(bind, links):
        op.create_index(index, links, [_TENANT_COLUMN, name_column])
    if not _has_table(bind, facts):
        op.create_table(
            facts,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(_PROJECT_COLUMN, sa.String(), nullable=False),
            sa.Column(name_column, sa.String(), nullable=False),
            sa.Column("linked", sa.Boolean(), nullable=False),
            sa.Column("set_at", UtcDateTime(), nullable=False),
            sa.Column("set_by", sa.String(), nullable=False),
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            sa.ForeignKeyConstraint(
                [_PROJECT_COLUMN, name_column],
                [f"{links}.project_id", f"{links}.{name_column}"],
                name=f"fk_{facts}_links",
            ),
            _tenant_fk(facts),
        )
    index = f"ix_{facts}_tenant_link"
    if index not in _index_names(bind, facts):
        op.create_index(index, facts, [_TENANT_COLUMN, _PROJECT_COLUMN, name_column, "id"])


def _create_tables(bind: sa.Connection) -> None:
    """Step 1: the six tables, each skipped when it exists."""
    if not _has_table(bind, _PROJECTS):
        op.create_table(
            _PROJECTS,
            sa.Column(_PROJECT_COLUMN, sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", UtcDateTime(), nullable=False),
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_PROJECT_COLUMN, name="pk_projects"),
            sa.UniqueConstraint(_TENANT_COLUMN, "slug", name="uq_projects_tenant_slug"),
            _tenant_fk(_PROJECTS),
        )
    if not _has_table(bind, _FORMER_SLUGS):
        op.create_table(
            _FORMER_SLUGS,
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column(_PROJECT_COLUMN, sa.String(), nullable=False),
            sa.Column("left_at", UtcDateTime(), nullable=False),
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_TENANT_COLUMN, "slug", name="pk_project_former_slugs"),
            _project_fk(_FORMER_SLUGS),
            _tenant_fk(_FORMER_SLUGS),
        )
    for records, name_column, _kind, links, facts in _LINKS:
        _create_link_tables(bind, records, name_column, links, facts)


_LINK_CREATED: list[dict[str, object]] = [{"field": "linked", "old": None, "new": True}]
"""A link's ``create`` diff, as the link domain writes it for a new link."""


def _change_row(
    tenant: sa.ColumnElement[str] | str,
    project: sa.ColumnElement[str] | str,
    kind: str,
    key: sa.ColumnElement[str] | str,
    diff: list[dict[str, object]],
    at: datetime,
) -> dict[str, object]:
    return {
        "recorded_at": sa.literal(at, type_=UtcDateTime()),
        "actor": sa.literal(_MIGRATION),
        "door": sa.literal(_MIGRATION),
        "record_kind": sa.literal(kind),
        "record_key": key,
        "revision": sa.literal(1),
        "op": sa.literal("create"),
        "diff": sa.literal(json.dumps(diff)),
        "apply_id": sa.null(),
        _TENANT_COLUMN: tenant,
        _PROJECT_COLUMN: project,
    }


def _insert_selected(table: sa.TableClause, row: dict[str, object], select: sa.Select) -> None:
    """``INSERT INTO table (row's names) SELECT row's values`` from ``select``'s FROM and WHERE."""
    bind = op.get_bind()
    names = list(row)
    bind.execute(sa.insert(table).from_select(names, select.with_only_columns(*row.values())))  # type: ignore[arg-type]


def _create_projects(bind: sa.Connection) -> None:
    """Step 3: a ``default`` project and its ``create`` change row for every tenant, in id order. A tenant already
    holding a project gets none; a project whose change row is missing gets it."""
    at = migration_clock().now()
    holding = set(bind.execute(sa.select(_projects.c.tenant_id).distinct()).scalars())
    for tenant in bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars().all():
        if tenant in holding:
            continue
        bind.execute(
            sa.insert(_projects).values(
                project_id=_mint_project_id(at),
                tenant_id=tenant,
                slug=_DEFAULT_SLUG,
                name=_DEFAULT_NAME,
                description="",
                revision=1,
                created_at=at,
            )
        )
    recorded = sa.exists().where(
        _changes.c.tenant_id == _projects.c.tenant_id,
        _changes.c.record_kind == "project",
        _changes.c.record_key == _projects.c.project_id,
    )
    diff = [
        {"field": "slug", "old": None, "new": _DEFAULT_SLUG},
        {"field": "name", "old": None, "new": _DEFAULT_NAME},
        {"field": "description", "old": None, "new": ""},
    ]
    row = _change_row(_projects.c.tenant_id, _projects.c.project_id, "project", _projects.c.project_id, diff, at)
    _insert_selected(_changes, row, sa.select(_projects).where(~recorded).order_by(_projects.c.tenant_id))


def _earliest_mints(bind: sa.Connection) -> dict[str, datetime]:
    """Each tenant holding a chunk, to its earliest chunk's ``minted_at``."""
    earliest = sa.func.min(_chunks.c.minted_at, type_=UtcDateTime()).label("earliest")
    rows = bind.execute(sa.select(_chunks.c.tenant_id, earliest).group_by(_chunks.c.tenant_id))
    return {row.tenant_id: row.earliest for row in rows}


def _link_records(bind: sa.Connection) -> None:
    """Step 4: every work source and every repository linked to its tenant's project — the link, its ``linked``
    fact and its ``create`` change row, each written only where it is missing."""
    at = migration_clock().now()
    earliest = _earliest_mints(bind)
    for records, name_column, kind, links_name, facts_name in _LINKS:
        links, facts, record = _link_tables(links_name, facts_name, records, name_column)
        project = _projects.c.project_id
        joined = record.join(_projects, _projects.c.tenant_id == record.c.tenant_id)
        unlinked = ~sa.exists().where(links.c.project_id == project, links.c[name_column] == record.c.name)
        _insert_selected(
            links,
            {
                _TENANT_COLUMN: record.c.tenant_id,
                _PROJECT_COLUMN: project,
                name_column: record.c.name,
                "revision": sa.literal(1),
            },
            sa.select(record).select_from(joined).where(unlinked).order_by(record.c.tenant_id, record.c.name),
        )
        tenants = bind.execute(sa.select(links.c.tenant_id).distinct().order_by(links.c.tenant_id)).scalars().all()
        for tenant in tenants:
            unfacted = ~sa.exists().where(
                facts.c.project_id == links.c.project_id, facts.c[name_column] == links.c[name_column]
            )
            _insert_selected(
                facts,
                {
                    _TENANT_COLUMN: links.c.tenant_id,
                    _PROJECT_COLUMN: links.c.project_id,
                    name_column: links.c[name_column],
                    "linked": sa.true(),
                    "set_at": sa.literal(earliest.get(tenant, at), type_=UtcDateTime()),
                    "set_by": sa.literal(_MIGRATION),
                },
                sa.select(links)
                .where(links.c.tenant_id == tenant, unfacted)
                .order_by(links.c.project_id, links.c[name_column]),
            )
        recorded = sa.exists().where(
            _changes.c.tenant_id == links.c.tenant_id,
            _changes.c.record_kind == kind,
            _changes.c.record_key == links.c[name_column],
            _changes.c.project_id == links.c.project_id,
        )
        row = _change_row(links.c.tenant_id, links.c.project_id, kind, links.c[name_column], _LINK_CREATED, at)
        _insert_selected(
            _changes, row, sa.select(links).where(~recorded).order_by(links.c.tenant_id, links.c[name_column])
        )


def _add_columns(bind: sa.Connection) -> None:
    """Step 2: a nullable ``project_id`` on each project-owned table and on the change log, added in place —
    before any change row is written, since a project's change row names its project."""
    for table in (*_OWNED, _CHANGES):
        if _PROJECT_COLUMN not in _columns(bind, table):
            op.add_column(table, sa.Column(_PROJECT_COLUMN, sa.String(), nullable=True))


def _fill(bind: sa.Connection, table: str, key: str, *, only: sa.ColumnElement[bool] | None = None) -> None:
    """Set ``project_id`` from the row's tenant's project on the rows where it is null, in one pass along ``key``, at
    most ``_BATCH_ROWS`` per statement, each page seeking past the last key the one before it covered."""
    t = sa.table(table, sa.column(key), sa.column(_TENANT_COLUMN, sa.String), sa.column(_PROJECT_COLUMN, sa.String))
    project = sa.select(_projects.c.project_id).where(_projects.c.tenant_id == t.c.tenant_id).scalar_subquery()
    unset = t.c[_PROJECT_COLUMN].is_(None)
    if only is not None:
        unset = sa.and_(unset, only)
    page = sa.select(t.c[key]).where(unset).order_by(t.c[key]).limit(_BATCH_ROWS)
    after: sa.ColumnElement[bool] = sa.true()
    while keys := bind.execute(page.where(after)).scalars().all():
        last = keys[-1]
        bind.execute(sa.update(t).where(unset, after, t.c[key] <= last).values({_PROJECT_COLUMN: project}))
        after = t.c[key] > last


def _stamp(bind: sa.Connection) -> None:
    """Step 5: every project-owned row, and every scope's and routine's change row, names its tenant's project."""
    for table, key in _OWNED.items():
        _fill(bind, table, key)
    _fill(bind, _CHANGES, "id", only=sa.column("record_kind", sa.String).in_(_PROJECT_KINDS))


def _tighten_table(bind: sa.Connection, table: str) -> None:
    columns = _columns(bind, table)
    require = table != _CHANGES and columns[_PROJECT_COLUMN]
    attach = f"fk_{table}_projects" not in _foreign_key_names(bind, table)
    uniques = _unique_names(bind, table)
    old_unique, new_unique, unique_columns = _UNIQUES.get(table, ("", "", ("", "")))
    drop_unique = bool(old_unique) and old_unique in uniques
    add_unique = bool(new_unique) and new_unique not in uniques
    if not (require or attach or drop_unique or add_unique):
        return
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        if require:
            batch.alter_column(_PROJECT_COLUMN, existing_type=sa.String(), nullable=False)
        if attach:
            batch.create_foreign_key(f"fk_{table}_projects", _PROJECTS, [_PROJECT_COLUMN], [_PROJECT_COLUMN])
        if drop_unique:
            batch.drop_constraint(old_unique, type_="unique")
        if add_unique:
            batch.create_unique_constraint(new_unique, list(unique_columns))


def _tighten(bind: sa.Connection) -> None:
    """Step 6: one batch per table — ``project_id`` required (on the change log, only its foreign key), and a
    scope's slug and a routine's name unique within the project."""
    for table in (*_OWNED, _CHANGES):
        _tighten_table(bind, table)


def _swap_indexes(bind: sa.Connection) -> None:
    """Step 7: the project-led indexes added beside the tenant-led ones, which keep serving the reads that name no
    project."""
    for name, table, columns in _ADDED_INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, list(columns))


def _verify(bind: sa.Connection) -> None:
    """Step 8: refuse to finish while a tenant holds other than one project or a project-owned row names none."""
    count = sa.select(sa.func.count()).where(_projects.c.tenant_id == _tenants.c.tenant_id).scalar_subquery()
    misplaced = bind.execute(sa.select(_tenants.c.tenant_id).where(count != 1).order_by(_tenants.c.tenant_id))
    tenants = list(misplaced.scalars())
    if tenants:
        raise RuntimeError(f"each tenant must hold exactly one project; {', '.join(tenants)} do not")
    for table in _OWNED:
        t = sa.table(table, sa.column(_TENANT_COLUMN, sa.String), sa.column(_PROJECT_COLUMN, sa.String))
        unset = bind.execute(
            sa.select(t.c.tenant_id).where(t.c.project_id.is_(None)).distinct().order_by(t.c.tenant_id)
        )
        named = list(unset.scalars())
        if named:
            raise RuntimeError(f"{table} rows of {', '.join(named)} name no project")
    unset_changes = sa.and_(_changes.c.project_id.is_(None), _changes.c.record_kind.in_(_PROJECT_KINDS))
    named = list(
        bind.execute(
            sa.select(_changes.c.tenant_id).where(unset_changes).distinct().order_by(_changes.c.tenant_id)
        ).scalars()
    )
    if named:
        raise RuntimeError(f"scope and routine change rows of {', '.join(named)} name no project")


#: The upgrade's steps, in order; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS: tuple[Callable[[sa.Connection], None], ...] = (
    _create_tables,
    _add_columns,
    _create_projects,
    _link_records,
    _stamp,
    _tighten,
    _swap_indexes,
    _verify,
)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


def _refuse_several_projects(bind: sa.Connection) -> None:
    tenant = _projects.c.tenant_id
    several = bind.execute(sa.select(tenant).group_by(tenant).having(sa.func.count() > 1).order_by(tenant))
    named = list(several.scalars())
    if named:
        raise RuntimeError(
            f"{', '.join(named)} each hold more than one project; a scope's slug and a routine's name could no longer"
            " be unique within the tenant — leave each tenant one project before downgrading"
        )


def _loosen_table(bind: sa.Connection, table: str) -> None:
    if _PROJECT_COLUMN not in _columns(bind, table):
        return
    uniques = _unique_names(bind, table)
    old_unique, new_unique, _unique_columns = _UNIQUES.get(table, ("", "", ("", "")))
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        if new_unique and new_unique in uniques:
            batch.drop_constraint(new_unique, type_="unique")
        if old_unique and old_unique not in uniques:
            batch.create_unique_constraint(old_unique, [_TENANT_COLUMN, _unique_column(table)])
        if f"fk_{table}_projects" in _foreign_key_names(bind, table):
            batch.drop_constraint(f"fk_{table}_projects", type_="foreignkey")
        batch.drop_column(_PROJECT_COLUMN)


def _unique_column(table: str) -> str:
    """The column a scope or routine is named by within its tenant: ``slug`` or ``name``."""
    return _UNIQUES[table][2][1]


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _PROJECTS):
        _refuse_several_projects(bind)
    for name, table, _ in _ADDED_INDEXES:
        if name in _index_names(bind, table):
            op.drop_index(name, table_name=table)
    bind.execute(sa.delete(_changes).where(_changes.c.record_kind.in_(_NEW_KINDS)))
    for table in (_CHANGES, *reversed(_OWNED)):
        _loosen_table(bind, table)
    for table in reversed(_NEW_TABLES):
        if _has_table(bind, table):
            op.drop_table(table)
