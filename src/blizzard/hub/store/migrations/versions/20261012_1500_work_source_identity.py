"""work source identity — work sources keyed by a hub-minted ``wsrc_`` id, a slug unique within its tenant, a free
display name, and every reference held by the id.

``work_sources`` gains ``work_source_id``, minted for each source as a prefixed ULID: its timestamp the source's
``created_at``, its random bits drawn from the source's tenant and old name, so re-running the upgrade mints the same
ids. It gains ``slug`` too: the old name when that is a slug (lowercase letters, digits and single inner hyphens, at
most 64) other than ``hub``, otherwise the name lowercased, each run outside ``[a-z0-9]`` collapsed to one hyphen,
trimmed and cut to 64 — ``work-source`` when nothing is left — and, where a tenant already holds it or it is ``hub``,
suffixed ``-2``, ``-3``, … The old name stays as the display ``name``, stripped and cut to 128 characters where it was
padded or longer, ``slug`` where nothing is left.

``work_source_lifecycle_facts``, ``project_source_links``, ``project_source_link_facts`` and
``forge_annotation_facts`` each gain ``work_source_id`` beside the name they carried, filled from the source their own
tenant holds under that name. A name a row of the first three carries that no source of its tenant holds refuses the
upgrade before anything is dropped, naming the table, the tenant and the names; an annotation fact naming no source
keeps the name it carried. The work-ref ``source`` of ``chunk_work_refs``, ``work_item_closures``, ``close_intents``
and ``garden_proposal_closures`` keeps its column: a value naming a source of its tenant becomes the source's id,
``hub`` stays ``hub``, and a value naming no source of its tenant is left as stored and logged. ``config_changes
.record_key`` of the ``work_source`` and ``project_source_link`` kinds is rewritten from the name to the id, tenant by
tenant; the diffs stay as recorded, and no revision moves and no change row is appended.

Then, in the order every dialect accepts: the facts, links and annotation facts drop their foreign keys onto the source
name and their name column, their id becoming required; ``project_source_links`` is keyed
``pk_project_source_links (project_id, work_source_id)`` and its indexes move onto the id; ``work_sources`` is rekeyed
``pk_work_sources (work_source_id)`` with ``uq_work_sources_tenant_slug (tenant_id, slug)``; each child gains its
foreign key onto the id. ``work_source_former_slugs`` is created beside ``work_sources``. Backfills page along a key in
bounded batches; every step inspects first and skips what a stopped run already did.

The downgrade restores the name keys: a source's name is its slug again — its display name is lost, and so are its
former slugs — every name reference and work ref is filled from the source the id names, the change rows' keys return
to names, and the ids and ``work_source_former_slugs`` are dropped.
Revision ID: 20261012_1500_work_source_identity
Revises: 20261012_1100_runner_bindings
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261012_1500_work_source_identity"
down_revision: str | None = "20261012_1100_runner_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

_BATCH_ROWS = 1000
_TENANT = "tenant_id"
_SOURCES = "work_sources"
_FORMER_SLUGS = "work_source_former_slugs"
_ID = "work_source_id"
_NAME = "source_name"
_PRIMARY_KEY = "pk_work_sources"
_TENANT_SLUG = "uq_work_sources_tenant_slug"
_FACTS = "work_source_lifecycle_facts"
_LINKS = "project_source_links"
_LINK_FACTS = "project_source_link_facts"
_ANNOTATIONS = "forge_annotation_facts"
_CHANGES = "config_changes"
#: The change-row kinds keyed by a work source's name before this revision and by its id after.
_KEYED_KINDS = ("work_source", "project_source_link")
#: The built-in source's key: never a configured source's slug, and a work ref naming it stays as it is.
_HUB = "hub"
#: Each table holding a work ref's ``source``, paged along its ``id``.
_WORK_REFS = ("chunk_work_refs", "work_item_closures", "close_intents", "garden_proposal_closures")

# The prefixed-ULID mint and the slug grammar, restated rather than imported (``bzh:frozen-revisions``): a type tag, an
# underscore, then 48 bits of millisecond timestamp and 80 further bits in Crockford base32.
_ID_PREFIX = "wsrc"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26
_RANDOM_BITS = 80
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_OUTSIDE_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 64
_NAME_MAX = 128
_FALLBACK_SLUG = "work-source"

_sources = sa.table(
    _SOURCES,
    sa.column(_ID, sa.String),
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column(_TENANT, sa.String),
    sa.column("created_at", UtcDateTime),
)
_changes = sa.table(
    _CHANGES,
    sa.column("id", sa.Integer),
    sa.column(_TENANT, sa.String),
    sa.column("record_kind", sa.String),
    sa.column("record_key", sa.String),
)

# Each table naming a work source, as this revision rekeys it — frozen here, never derived from ``schema.metadata``:
# the table; the key its backfills page along; the foreign key onto the source (or link) name it drops, ``None`` for a
# table without one; the foreign key onto the id it gains, with its target columns; whether a name no source holds
# refuses the upgrade (a table without a foreign key keeps the name it carried instead).
_Child = tuple[str, tuple[str, ...], str | None, tuple[str, str, tuple[str, ...], tuple[str, ...]] | None, bool]
_CHILDREN: tuple[_Child, ...] = (
    (
        _FACTS,
        ("id",),
        "fk_work_source_lifecycle_facts_work_sources",
        ("fk_work_source_lifecycle_facts_work_sources", _SOURCES, (_ID,), (_ID,)),
        True,
    ),
    (
        _LINKS,
        ("project_id",),
        "fk_project_source_links_work_sources",
        ("fk_project_source_links_work_sources", _SOURCES, (_ID,), (_ID,)),
        True,
    ),
    (
        _LINK_FACTS,
        ("id",),
        "fk_project_source_link_facts_links",
        ("fk_project_source_link_facts_links", _LINKS, ("project_id", _ID), ("project_id", _ID)),
        True,
    ),
    (_ANNOTATIONS, ("id",), None, None, False),
)
#: The tables whose name column is ``name`` rather than ``source_name``.
_NAME_COLUMN = {_FACTS: "name"}

#: Each table's index over the source: its name before and after this revision, and its columns, ``None`` standing for
#: the table's source column.
_INDEXES: dict[str, tuple[str, str, tuple[str | None, ...]]] = {
    _LINKS: ("ix_project_source_links_tenant_source", "ix_project_source_links_tenant_source", (_TENANT, None)),
    _LINK_FACTS: (
        "ix_project_source_link_facts_tenant_link",
        "ix_project_source_link_facts_tenant_link",
        (_TENANT, "project_id", None, "id"),
    ),
    _ANNOTATIONS: (
        "ix_forge_annotation_facts_tenant_source_name_id",
        "ix_forge_annotation_facts_tenant_work_source_id_id",
        (_TENANT, None, "id"),
    ),
}


def _name_column(table: str) -> str:
    return _NAME_COLUMN.get(table, _NAME)


def _index_columns(table: str, source: str) -> list[str]:
    return [source if column is None else column for column in _INDEXES[table][2]]


def _encode(value: int) -> str:
    chars = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _drawn(key: str) -> int:
    """``_RANDOM_BITS`` bits drawn from ``key`` — the same on every run."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[: _RANDOM_BITS // 8], "big")


def _minted(tenant_id: str, name: str, created_at: datetime) -> str:
    """The id of the source ``tenant_id`` holds under ``name``: timestamped at its creation, the same every run."""
    at = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    millis = max(0, (at - _EPOCH) // timedelta(milliseconds=1))
    return f"{_ID_PREFIX}_{_encode((millis << _RANDOM_BITS) | _drawn(f'work_source/{tenant_id}/{name}'))}"


def _is_slug(name: str) -> bool:
    """Whether ``name`` is a slug a work source may keep: in the grammar, within the bound, and not ``hub``."""
    return len(name) <= _SLUG_MAX and bool(_SLUG.match(name)) and name != _HUB


def _derived(name: str) -> str:
    """The slug closest to ``name``: lowercased, each run outside ``[a-z0-9]`` one hyphen, trimmed, cut to the bound."""
    derived = _OUTSIDE_SLUG.sub("-", name.lower()).strip("-")[:_SLUG_MAX].strip("-")
    return derived or _FALLBACK_SLUG


def _suffixed(base: str, taken: set[str]) -> str:
    """``base``, or the first ``base-2``, ``base-3``, … no source of the tenant holds and that is not ``hub``, cut to
    the bound."""
    if base not in taken and base != _HUB:
        return base
    number = 2
    while True:
        tail = f"-{number}"
        candidate = f"{base[: _SLUG_MAX - len(tail)].rstrip('-')}{tail}"
        if candidate not in taken:
            return candidate
        number += 1


def _display(name: str, slug: str) -> str:
    """``name`` as a display name: stripped and cut to the bound where padded or longer, ``slug`` where nothing is left."""
    shown = name.strip()[:_NAME_MAX].strip()
    return shown or slug


def _columns(bind: sa.Connection, table: str) -> dict[str, bool]:
    """Each column of ``table`` mapped to whether it is nullable."""
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(bind).get_columns(table)}


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(bind).get_indexes(table)}


def _foreign_keys(bind: sa.Connection, table: str) -> set[str | None]:
    return {fk.get("name") for fk in sa.inspect(bind).get_foreign_keys(table)}


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps the unnamed ``CHECK`` its own reflection drops."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def _rekey(reflected: sa.Table, columns: Sequence[str]) -> None:
    """Flag ``columns`` as the copy's primary key, and no others."""
    for column in reflected.columns:
        column.primary_key = column.name in columns


def _add_column(bind: sa.Connection, table: str, column: str) -> None:
    """Add ``column`` to ``table``, nullable — in place on every dialect."""
    if column in _columns(bind, table):
        return
    with op.batch_alter_table(table, recreate="auto") as batch:
        batch.add_column(sa.Column(column, sa.String(), nullable=True))


def _fill(
    bind: sa.Connection, table: sa.TableClause, key: tuple[str, ...], column: str, value: sa.ColumnElement[str]
) -> None:
    """Set ``column`` from ``value`` on the rows where it is null, in one pass along ``key``, at most ``_BATCH_ROWS``
    per statement. Each page seeks past the last key the page before it covered; a row ``value`` leaves null is passed
    over, never revisited."""
    columns = [table.c[name] for name in key]
    cursor = sa.tuple_(*columns) if len(columns) > 1 else columns[0]
    unset = table.c[column].is_(None)
    page = sa.select(*columns).where(unset).order_by(*columns).limit(_BATCH_ROWS)
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        last = tuple(rows[-1]) if len(columns) > 1 else rows[-1][0]
        bind.execute(sa.update(table).where(unset, after, cursor <= last).values({column: value}))
        after = cursor > last


def _rewrite(
    bind: sa.Connection,
    table: sa.TableClause,
    column: str,
    conditions: list[sa.ColumnElement[bool]],
    value: sa.Select,
) -> None:
    """Set ``column`` of the rows ``conditions`` select to ``value``, a page of row ids at a time."""
    page = sa.select(table.c.id).where(*conditions).order_by(table.c.id).limit(_BATCH_ROWS)
    last = 0
    while rows := bind.execute(page.where(table.c.id > last)).scalars().all():
        bind.execute(
            sa.update(table)
            .where(*conditions, table.c.id > last, table.c.id <= rows[-1])
            .values({column: value.correlate(table).scalar_subquery()})
        )
        last = rows[-1]


def _work_ref_table(table: str) -> sa.TableClause:
    return sa.table(table, sa.column("id", sa.Integer), sa.column("source", sa.String), sa.column(_TENANT, sa.String))


# --- upgrade ---------------------------------------------------------------------------------------------------------


def _create_former_slugs(bind: sa.Connection) -> None:
    if sa.inspect(bind).has_table(_FORMER_SLUGS):
        return
    op.create_table(
        _FORMER_SLUGS,
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column(_ID, sa.String(), nullable=False),
        sa.Column("left_at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint(_TENANT, "slug", name="pk_work_source_former_slugs"),
        sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name="fk_work_source_former_slugs_tenant_id"),
    )


def _mint(bind: sa.Connection) -> None:
    """Give every source still without them its id and slug, tenant by tenant, a page at a time along
    ``(tenant_id, name)``. A name that is a slug keeps it; a derived slug avoids every slug its tenant holds."""
    _add_column(bind, _SOURCES, _ID)
    _add_column(bind, _SOURCES, "slug")
    s = _sources
    taken: dict[str, set[str]] = {}
    for tenant_id, slug in bind.execute(sa.select(s.c.tenant_id, s.c.slug).where(s.c.slug.is_not(None))):
        taken.setdefault(tenant_id, set()).add(slug)
    key = sa.tuple_(s.c.tenant_id, s.c.name)
    page = (
        sa.select(s.c.tenant_id, s.c.name, s.c.created_at, s.c.slug)
        .where(sa.or_(s.c.work_source_id.is_(None), s.c.slug.is_(None)))
        .order_by(s.c.tenant_id, s.c.name)
        .limit(_BATCH_ROWS)
    )
    stamp = (
        sa.update(s)
        .where(s.c.tenant_id == sa.bindparam("b_tenant"), s.c.name == sa.bindparam("b_name"))
        .values({_ID: sa.bindparam("b_id"), "slug": sa.bindparam("b_slug")})
    )
    # Names that are slugs claim theirs before any derived slug looks for a free one.
    for tenant_id, name in bind.execute(
        sa.select(s.c.tenant_id, s.c.name).where(s.c.slug.is_(None)).order_by(s.c.tenant_id, s.c.name)
    ):
        if _is_slug(name):
            taken.setdefault(tenant_id, set()).add(name)
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        stamps = []
        for tenant_id, name, created_at, slug in rows:
            held = taken.setdefault(tenant_id, set())
            if slug is None:
                if _is_slug(name):
                    slug = name
                else:
                    slug = _suffixed(_derived(name), held)
                    held.add(slug)
            stamps.append(
                {"b_tenant": tenant_id, "b_name": name, "b_id": _minted(tenant_id, name, created_at), "b_slug": slug}
            )
        bind.execute(stamp, stamps)
        after = key > (rows[-1][0], rows[-1][1])


def _child_table(table: str, key: tuple[str, ...], name: str) -> sa.TableClause:
    return sa.table(table, *(sa.column(c, sa.String) for c in dict.fromkeys((*key, name, _ID, _TENANT))))


def _carry_ids(bind: sa.Connection, child: _Child) -> None:
    """Give each row of the child the id of the source its own tenant holds under the name it carries; an annotation
    fact naming no source keeps that name."""
    table, key, _, _, refuses = child
    _add_column(bind, table, _ID)
    name = _name_column(table)
    if name not in _columns(bind, table):
        return
    rows = _child_table(table, key, name)
    held = (
        sa.select(_sources.c.work_source_id)
        .where(_sources.c.tenant_id == rows.c.tenant_id, _sources.c.name == rows.c[name])
        .correlate(rows)
        .scalar_subquery()
    )
    page_key = (*key, name) if table == _LINKS else key
    _fill(bind, rows, page_key, _ID, held)
    if refuses:
        return
    kept = bind.execute(
        sa.select(rows.c.tenant_id, rows.c[name])
        .where(rows.c[_ID].is_(None))
        .distinct()
        .order_by(rows.c.tenant_id, rows.c[name])
    ).all()
    if kept:
        _log.warning(
            "%s: %d source names no work source of their tenant holds are kept as stored: %s",
            table,
            len(kept),
            ", ".join(f"{tenant}/{value}" for tenant, value in kept[:5]),
        )
    _fill(bind, rows, page_key, _ID, rows.c[name])


def _carry_work_refs(bind: sa.Connection) -> None:
    """Rewrite every work ref naming a source of its tenant to the source's id; ``hub`` stays, and a value naming no
    source of its tenant is left as stored and logged."""
    s = _sources
    for table in _WORK_REFS:
        refs = _work_ref_table(table)
        named = sa.select(s.c.work_source_id).where(s.c.tenant_id == refs.c.tenant_id, s.c.name == refs.c.source)
        held = sa.exists().where(s.c.tenant_id == refs.c.tenant_id, s.c.work_source_id == refs.c.source)
        unresolved = [refs.c.source.is_not(None), refs.c.source != _HUB, ~held]
        _rewrite(bind, refs, "source", [*unresolved, sa.exists(named)], named)
        stranded = bind.execute(
            sa.select(refs.c.tenant_id, refs.c.source)
            .where(*unresolved)
            .distinct()
            .order_by(refs.c.tenant_id, refs.c.source)
        ).all()
        if stranded:
            _log.warning(
                "%s: %d work-ref sources name no work source of their tenant and are left as stored: %s",
                table,
                len(stranded),
                ", ".join(f"{tenant}/{value}" for tenant, value in stranded[:5]),
            )


def _carry_change_keys(bind: sa.Connection) -> None:
    """Rewrite the key of every work-source and source-link change row from the source's name to its id, tenant by
    tenant, along the row id; a key that is already an id of the tenant is left."""
    c, s = _changes, _sources
    named = sa.select(s.c.work_source_id).where(s.c.tenant_id == c.c.tenant_id, s.c.name == c.c.record_key)
    held = sa.exists().where(s.c.tenant_id == c.c.tenant_id, s.c.work_source_id == c.c.record_key)
    _rewrite(bind, c, "record_key", [c.c.record_kind.in_(_KEYED_KINDS), ~held, sa.exists(named)], named)


def _refuse_dangling(bind: sa.Connection) -> None:
    """Refuse — before anything is rewritten or dropped — a name a row of a table with a foreign key onto the source
    carries that no source of its tenant holds."""
    dangling: list[str] = []
    for table, _, _, _, refuses in _CHILDREN:
        name = _name_column(table)
        if not refuses or name not in _columns(bind, table):
            continue
        child = sa.table(table, sa.column(name, sa.String), sa.column(_ID, sa.String), sa.column(_TENANT, sa.String))
        rows = bind.execute(
            sa.select(child.c.tenant_id, child.c[name])
            .where(child.c[name].is_not(None), child.c.work_source_id.is_(None))
            .distinct()
            .order_by(child.c.tenant_id, child.c[name])
            .limit(5)
        ).all()
        dangling.extend(f"{table}: tenant {tenant} names work source {value!r}" for tenant, value in rows)
    if dangling:
        raise RuntimeError(
            "a work source name no work source of its tenant holds cannot become an id; "
            + "; ".join(dangling)
            + " — create the work source, or remove the row, then upgrade again"
        )


def _normalize_names(bind: sa.Connection) -> None:
    """Make every display name a valid one: stripped and cut to the bound where padded or longer."""
    s = _sources
    page = (
        sa.select(s.c.work_source_id, s.c.name, s.c.slug)
        .where(sa.or_(sa.func.length(s.c.name) > _NAME_MAX, s.c.name != sa.func.trim(s.c.name), s.c.name == ""))
        .order_by(s.c.work_source_id)
        .limit(_BATCH_ROWS)
    )
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        for work_source_id, name, slug in rows:
            bind.execute(sa.update(s).where(s.c.work_source_id == work_source_id).values(name=_display(name, slug)))
        after = s.c.work_source_id > rows[-1][0]


def _detach(bind: sa.Connection, child: _Child) -> None:
    """One batch taking ``table`` off the source name: the foreign key onto it dropped, the name column too, the id
    required, and a table's key and index moved onto the id."""
    table, _, old_foreign_key, _, _ = child
    name = _name_column(table)
    columns = _columns(bind, table)
    drop_foreign_key = old_foreign_key is not None and old_foreign_key in _foreign_keys(bind, table)
    drop_name = name in columns
    tighten = columns[_ID]
    indexes = _index_names(bind, table)
    index = _INDEXES.get(table)
    drop_index = index is not None and index[0] in indexes and drop_name
    create_index = index is not None and (drop_index or index[1] not in indexes)
    primary_key = sa.inspect(bind).get_pk_constraint(table)
    rekey = table == _LINKS and primary_key["constrained_columns"] != ["project_id", _ID]
    if not (drop_foreign_key or drop_name or tighten or drop_index or create_index or rekey):
        return
    _clear_stale_copy(bind, table)
    reflected = _reflected(bind, table)
    if rekey and not primary_key.get("name"):
        _rekey(reflected, ("project_id", _ID))
    with op.batch_alter_table(table, recreate="auto", copy_from=reflected) as batch:
        if drop_foreign_key and old_foreign_key is not None:
            batch.drop_constraint(old_foreign_key, type_="foreignkey")
        if drop_index and index is not None:
            batch.drop_index(index[0])
        if rekey:
            if old_name := primary_key.get("name"):
                batch.drop_constraint(old_name, type_="primary")
            batch.create_primary_key("pk_project_source_links", ["project_id", _ID])
        if drop_name:
            batch.drop_column(name)
        if tighten:
            batch.alter_column(_ID, existing_type=sa.String(), nullable=False)
        if create_index and index is not None:
            batch.create_index(index[1], _index_columns(table, _ID))


def _rekey_sources(bind: sa.Connection) -> None:
    """One batch keying ``work_sources`` by its id: ``pk_work_sources``, the slug unique within its tenant."""
    inspector = sa.inspect(bind)
    primary_key = inspector.get_pk_constraint(_SOURCES)
    rekey = primary_key.get("name") != _PRIMARY_KEY or primary_key["constrained_columns"] != [_ID]
    add_unique = _TENANT_SLUG not in {u["name"] for u in inspector.get_unique_constraints(_SOURCES)}
    columns = _columns(bind, _SOURCES)
    if not (rekey or add_unique or columns[_ID] or columns["slug"]):
        return
    _clear_stale_copy(bind, _SOURCES)
    reflected = _reflected(bind, _SOURCES)
    if rekey:
        _rekey(reflected, (_ID,))
    with op.batch_alter_table(_SOURCES, recreate="auto", copy_from=reflected) as batch:
        if rekey:
            if old_name := primary_key.get("name"):
                batch.drop_constraint(old_name, type_="primary")
            batch.create_primary_key(_PRIMARY_KEY, [_ID])
        for column in (_ID, "slug"):
            if columns[column]:
                batch.alter_column(column, existing_type=sa.String(), nullable=False)
        if add_unique:
            batch.create_unique_constraint(_TENANT_SLUG, [_TENANT, "slug"])


def _attach(bind: sa.Connection, child: _Child) -> None:
    """Add the foreign key from the id onto its target, unless the table carries it already."""
    table, _, _, foreign_key, _ = child
    if foreign_key is None or foreign_key[0] in _foreign_keys(bind, table):
        return
    name, target, local, remote = foreign_key
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        batch.create_foreign_key(name, target, list(local), list(remote))


def _attach_former_slugs(bind: sa.Connection) -> None:
    """Add ``fk_work_source_former_slugs_work_sources`` once ``work_sources`` is keyed by its id."""
    name = "fk_work_source_former_slugs_work_sources"
    if name in _foreign_keys(bind, _FORMER_SLUGS):
        return
    with op.batch_alter_table(_FORMER_SLUGS, recreate="auto", copy_from=_reflected(bind, _FORMER_SLUGS)) as batch:
        batch.create_foreign_key(name, _SOURCES, [_ID], [_ID])


def _carry(bind: sa.Connection) -> None:
    """Every reference carries the id beside the name; the dangling are refused before any value is rewritten; the work
    refs and change keys are rewritten; the display names are made valid."""
    for child in _CHILDREN:
        _carry_ids(bind, child)
    _refuse_dangling(bind)
    _carry_work_refs(bind)
    _carry_change_keys(bind)
    _normalize_names(bind)


def _detach_all(bind: sa.Connection) -> None:
    """The link facts leave the links' key before the links are rekeyed, so the children go in reverse."""
    for child in reversed(_CHILDREN):
        _detach(bind, child)


def _attach_all(bind: sa.Connection) -> None:
    for child in _CHILDREN:
        _attach(bind, child)
    _attach_former_slugs(bind)


#: In the order the upgrade runs them; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS = (_create_former_slugs, _mint, _carry, _detach_all, _rekey_sources, _attach_all)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


# --- downgrade -------------------------------------------------------------------------------------------------------


def _unattach(bind: sa.Connection, child: _Child) -> None:
    """Drop the foreign key onto the id, unless the table no longer carries it."""
    table, _, _, foreign_key, _ = child
    if foreign_key is None or foreign_key[0] not in _foreign_keys(bind, table):
        return
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        batch.drop_constraint(foreign_key[0], type_="foreignkey")


def _carry_names(bind: sa.Connection, child: _Child) -> None:
    """Give each row of the child back the slug of the source its id names, as its name; an annotation fact naming no
    source keeps the value it carried."""
    table, key, _, _, refuses = child
    name = _name_column(table)
    _add_column(bind, table, name)
    if _ID not in _columns(bind, table):
        return
    rows = _child_table(table, key, name)
    resolved = (
        sa.select(_sources.c.slug)
        .where(_sources.c.work_source_id == rows.c[_ID])
        .correlate(rows)
        .scalar_subquery()
    )
    page_key = (*key, _ID) if table == _LINKS else key
    _fill(bind, rows, page_key, name, resolved)
    if not refuses:
        _fill(bind, rows, page_key, name, rows.c[_ID])


def _restore_work_refs(bind: sa.Connection) -> None:
    """Rewrite every work ref naming a source by its id back to the source's slug."""
    s = _sources
    for table in _WORK_REFS:
        refs = _work_ref_table(table)
        slug = sa.select(s.c.slug).where(s.c.tenant_id == refs.c.tenant_id, s.c.work_source_id == refs.c.source)
        _rewrite(bind, refs, "source", [sa.exists(slug)], slug)


def _restore_change_keys(bind: sa.Connection) -> None:
    """Rewrite every work-source and source-link change row's key from the source's id back to its slug."""
    c, s = _changes, _sources
    slug = sa.select(s.c.slug).where(s.c.tenant_id == c.c.tenant_id, s.c.work_source_id == c.c.record_key)
    _rewrite(bind, c, "record_key", [c.c.record_kind.in_(_KEYED_KINDS), sa.exists(slug)], slug)


def _reattach(bind: sa.Connection, child: _Child) -> None:
    """One batch putting ``table`` back on the source name: the name required, the key and index on it, the id
    dropped."""
    table, *_ = child
    name = _name_column(table)
    columns = _columns(bind, table)
    index = _INDEXES.get(table)
    primary_key = sa.inspect(bind).get_pk_constraint(table)
    rekey = table == _LINKS and primary_key["constrained_columns"] != ["project_id", name]
    drop_id = _ID in columns
    tighten = columns[name]
    if not (rekey or drop_id or tighten):
        return
    _clear_stale_copy(bind, table)
    reflected = _reflected(bind, table)
    if rekey and not primary_key.get("name"):
        _rekey(reflected, ("project_id", name))
    drop_index = index is not None and index[1] in _index_names(bind, table) and drop_id
    with op.batch_alter_table(table, recreate="auto", copy_from=reflected) as batch:
        if drop_index and index is not None:
            batch.drop_index(index[1])
        if rekey:
            if old_name := primary_key.get("name"):
                batch.drop_constraint(old_name, type_="primary")
            batch.create_primary_key("pk_project_source_links", ["project_id", name])
        if drop_id:
            batch.drop_column(_ID)
        if tighten:
            batch.alter_column(name, existing_type=sa.String(), nullable=False)
        if drop_index and index is not None:
            batch.create_index(index[0], _index_columns(table, name))


def _unkey_sources(bind: sa.Connection) -> None:
    """One batch keying ``work_sources`` by ``(tenant_id, name)`` again, its name its slug, without the id and slug."""
    s = _sources
    bind.execute(sa.update(s).values(name=s.c.slug))
    inspector = sa.inspect(bind)
    primary_key = inspector.get_pk_constraint(_SOURCES)
    rekey = primary_key["constrained_columns"] != [_TENANT, "name"]
    drop_unique = _TENANT_SLUG in {u["name"] for u in inspector.get_unique_constraints(_SOURCES)}
    columns = _columns(bind, _SOURCES)
    drop = [c for c in (_ID, "slug") if c in columns]
    if not (rekey or drop_unique or drop):
        return
    _clear_stale_copy(bind, _SOURCES)
    reflected = _reflected(bind, _SOURCES)
    if rekey:
        _rekey(reflected, (_TENANT, "name"))
    with op.batch_alter_table(_SOURCES, recreate="auto", copy_from=reflected) as batch:
        if drop_unique:
            batch.drop_constraint(_TENANT_SLUG, type_="unique")
        if rekey:
            if old_name := primary_key.get("name"):
                batch.drop_constraint(old_name, type_="primary")
            batch.create_primary_key(_PRIMARY_KEY, [_TENANT, "name"])
        for column in drop:
            batch.drop_column(column)


def _name_foreign_key(bind: sa.Connection, table: str) -> None:
    """Add the foreign key from the name columns back onto the name key, where the table had one."""
    if table == _FACTS:
        name, target, local, remote = (
            "fk_work_source_lifecycle_facts_work_sources",
            _SOURCES,
            [_TENANT, "name"],
            [_TENANT, "name"],
        )
    elif table == _LINKS:
        name, target, local, remote = (
            "fk_project_source_links_work_sources",
            _SOURCES,
            [_TENANT, _NAME],
            [_TENANT, "name"],
        )
    elif table == _LINK_FACTS:
        name, target, local, remote = (
            "fk_project_source_link_facts_links",
            _LINKS,
            ["project_id", _NAME],
            ["project_id", _NAME],
        )
    else:
        return
    if name in _foreign_keys(bind, table):
        return
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        batch.create_foreign_key(name, target, local, remote)


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_FORMER_SLUGS):
        op.drop_table(_FORMER_SLUGS)
    for child in _CHILDREN:
        _unattach(bind, child)
    for child in _CHILDREN:
        _carry_names(bind, child)
    _restore_work_refs(bind)
    _restore_change_keys(bind)
    for child in _CHILDREN:
        _reattach(bind, child)
    _unkey_sources(bind)
    for table, *_ in _CHILDREN:
        _name_foreign_key(bind, table)
