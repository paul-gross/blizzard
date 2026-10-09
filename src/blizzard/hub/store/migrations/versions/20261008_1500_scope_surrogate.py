"""scope surrogate — scopes keyed by a hub-minted ``scp_`` id, a slug unique within its tenant, every link by the id.

``scopes`` gains ``scope_id``, minted for each scope as a prefixed ULID: its timestamp the scope's ``created_at``, its
random bits drawn from the scope's tenant and slug, so re-running the upgrade mints the same ids. Each of the six tables
naming a scope by slug gains the id beside it, filled from the scope its own tenant holds under that slug. A slug a row
names but no scope of the row's tenant holds — the hub never enforced its foreign keys on SQLite — gets a stand-in
scope, minted as the hub mints a scope a routine names unseen: no description, revision 1; its ``created_at`` the epoch
and its id drawn from a namespace no other scope's id is, so the id alone marks it. The stand-ins are staged in
``scope_stand_ins`` until ``scopes`` is rekeyed, then added to it and their count logged. Then, in the order every
dialect accepts: each child drops its foreign key onto ``scopes.slug`` and, except ``findings`` and ``finding_sets``
(which keep ``scope_slug`` as their label), the slug column, its id becoming required; ``scopes`` is rekeyed —
``pk_scopes (scope_id)``, ``uq_scopes_tenant_slug (tenant_id, slug)``, no ``ix_scopes_tenant_id`` (the unique
constraint leads with ``tenant_id``); each child gains ``fk_<child>_scopes`` onto the id. ``routine_scopes`` is keyed
``pk_routine_scopes (routine_id, scope_id)`` with ``ix_routine_scopes_scope_id``. Backfills page along a key in bounded
batches; every step inspects first and skips what a stopped run already did.

The downgrade refuses while more than one tenant exists, naming them; with one it restores the slug links, deletes the
stand-in scopes, restores the unnamed slug keys and foreign keys and the indexes those tables had before, and drops
every ``scope_id``.
Revision ID: 20261008_1500_scope_surrogate
Revises: 20261008_1400_tenant_tighten
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_1500_scope_surrogate"
down_revision: str | None = "20261008_1400_tenant_tighten"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_SCOPES = "scopes"
_SCOPE_ID = "scope_id"
_TENANT_COLUMN = "tenant_id"
_PRIMARY_KEY = "pk_scopes"
_TENANT_SLUG = "uq_scopes_tenant_slug"
_TENANT_INDEX = "ix_scopes_tenant_id"
_STAND_INS = "scope_stand_ins"
_STAND_INS_KEY = "pk_scope_stand_ins"
_STAND_IN_NAMESPACE = "stand-in"

_log = logging.getLogger("alembic.runtime.migration")

# The prefixed-ULID mint, restated rather than imported (``bzh:frozen-revisions``): a type tag, an underscore, then 48
# bits of millisecond timestamp and 80 further bits in Crockford base32.
_SCOPE_PREFIX = "scp"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26
_RANDOM_BITS = 80
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# Each table naming a scope, as this revision relinks it — frozen here, never derived from ``schema.metadata``: the
# table; the column naming the scope by slug before this revision; the column naming it by id after; the primary key's
# columns besides the link, which the backfills page by; the rebuilt primary key's name, where the link is part of it;
# the index on the link column, named before and after; whether the slug stays beside the id as the row's label.
_Link = tuple[str, str, str, tuple[str, ...], str | None, tuple[str, str] | None, bool]
_LINKS: tuple[_Link, ...] = (
    ("scope_lifecycle_facts", "slug", "scope_id", ("id",), None, None, False),
    ("routines", "default_scope_slug", "default_scope_id", ("routine_id",), None, None, False),
    (
        "routine_scopes",
        "scope_slug",
        "scope_id",
        ("routine_id",),
        "pk_routine_scopes",
        ("ix_routine_scopes_scope_slug", "ix_routine_scopes_scope_id"),
        False,
    ),
    ("work_item_runs", "scope_slug", "scope_id", ("work_item_id",), None, None, False),
    ("findings", "scope_slug", "scope_id", ("finding_id",), None, None, True),
    ("finding_sets", "scope_slug", "scope_id", ("finding_set_id",), None, None, True),
)

_tenants = sa.table("tenants", sa.column(_TENANT_COLUMN, sa.String))
_scopes = sa.table(
    _SCOPES,
    sa.column(_SCOPE_ID, sa.String),
    sa.column("slug", sa.String),
    sa.column(_TENANT_COLUMN, sa.String),
    sa.column("description", sa.Text),
    sa.column("created_at", sa.DateTime),
    sa.column("revision", sa.Integer),
)
_stand_ins = sa.table(
    _STAND_INS, sa.column(_TENANT_COLUMN, sa.String), sa.column("slug", sa.String), sa.column(_SCOPE_ID, sa.String)
)


def _foreign_key(table: str) -> str:
    return f"fk_{table}_scopes"


def _tenant_ids(bind: sa.Connection) -> list[str]:
    return list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())


def _columns(bind: sa.Connection, table: str) -> dict[str, bool]:
    """Each column of ``table`` mapped to whether it is nullable."""
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(bind).get_columns(table)}


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(bind).get_indexes(table)}


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
    per statement.

    Each page seeks past the last key the page before it covered instead of scanning from the start, so the work grows
    with the row count, not its square; a row ``value`` leaves null is passed over, never revisited.
    """
    columns = [table.c[name] for name in key]
    cursor = sa.tuple_(*columns) if len(columns) > 1 else columns[0]
    unset = table.c[column].is_(None)
    page = sa.select(*columns).where(unset).order_by(*columns).limit(_BATCH_ROWS)
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        last = tuple(rows[-1]) if len(columns) > 1 else rows[-1][0]
        bind.execute(sa.update(table).where(unset, after, cursor <= last).values({column: value}))
        after = cursor > last


def _encode(value: int) -> str:
    chars = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _drawn(key: str) -> int:
    """``_RANDOM_BITS`` bits drawn from ``key`` — the same on every run."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[: _RANDOM_BITS // 8], "big")


def _minted(tenant_id: str, slug: str, created_at: datetime) -> str:
    """The id of the scope ``tenant_id`` holds under ``slug``: timestamped at its creation, the same on every run."""
    at = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    millis = (at - _EPOCH) // timedelta(milliseconds=1)
    return f"{_SCOPE_PREFIX}_{_encode((millis << _RANDOM_BITS) | _drawn(f'{tenant_id}/{slug}'))}"


def _stand_in_id(tenant_id: str, slug: str) -> str:
    """The id of the stand-in scope for ``slug`` in ``tenant_id``: timestamped at the epoch, its random bits drawn from
    the stand-in namespace, which no held scope's key falls in (a slug holds no ``/``)."""
    return f"{_SCOPE_PREFIX}_{_encode(_drawn(f'{_STAND_IN_NAMESPACE}/{tenant_id}/{slug}'))}"


def _mint_scope_ids(bind: sa.Connection) -> None:
    """Give every scope still without one its id, a page at a time along ``(tenant_id, slug)``."""
    _add_column(bind, _SCOPES, _SCOPE_ID)
    key = sa.tuple_(_scopes.c.tenant_id, _scopes.c.slug)
    page = (
        sa.select(_scopes.c.tenant_id, _scopes.c.slug, _scopes.c.created_at)
        .where(_scopes.c.scope_id.is_(None))
        .order_by(_scopes.c.tenant_id, _scopes.c.slug)
        .limit(_BATCH_ROWS)
    )
    stamp = (
        sa.update(_scopes)
        .where(_scopes.c.tenant_id == sa.bindparam("b_tenant"), _scopes.c.slug == sa.bindparam("b_slug"))
        .values({_SCOPE_ID: sa.bindparam("b_id")})
    )
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        bind.execute(
            stamp,
            [
                {"b_tenant": tenant_id, "b_slug": slug, "b_id": _minted(tenant_id, slug, created_at)}
                for tenant_id, slug, created_at in rows
            ],
        )
        after = key > (rows[-1][0], rows[-1][1])


def _child(link: _Link, cursor: str) -> sa.TableClause:
    """A narrow stub of the linked table: its key, ``cursor`` beside it, both link columns and the tenant."""
    table, slug, id_column, key, *_ = link
    names = dict.fromkeys((*key, cursor, slug, id_column, _TENANT_COLUMN))
    return sa.table(table, *(sa.column(name, sa.String) for name in names))


def _carry_ids(bind: sa.Connection, link: _Link) -> None:
    """Give each row of the linked table the id of the scope its own tenant holds under the slug it names."""
    table, slug, id_column, key, key_name, *_ = link
    _add_column(bind, table, id_column)
    if slug not in _columns(bind, table):
        return
    child = _child(link, slug)
    held = (
        sa.select(_scopes.c.scope_id)
        .where(_scopes.c.tenant_id == child.c.tenant_id, _scopes.c.slug == child.c[slug])
        .correlate(child)
        .scalar_subquery()
    )
    stand_in = (
        sa.select(_stand_ins.c.scope_id)
        .where(_stand_ins.c.tenant_id == child.c.tenant_id, _stand_ins.c.slug == child.c[slug])
        .correlate(child)
        .scalar_subquery()
    )
    page_key = (*key, slug) if key_name else key
    _fill(bind, child, page_key, id_column, sa.func.coalesce(held, stand_in))


def _stage_stand_ins(bind: sa.Connection) -> None:
    """Stage a stand-in for each ``(tenant_id, slug)`` a row names but no scope of its tenant holds, a page at a time
    along that pair per linked table, before any linked table is touched."""
    if not sa.inspect(bind).has_table(_STAND_INS):
        op.create_table(
            _STAND_INS,
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column(_SCOPE_ID, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_TENANT_COLUMN, "slug", name=_STAND_INS_KEY),
        )
    for link in _LINKS:
        table, slug, *_ = link
        if slug not in _columns(bind, table):
            continue
        child = _child(link, slug)
        named = (child.c.tenant_id, child.c[slug])
        held = sa.exists().where(_scopes.c.tenant_id == child.c.tenant_id, _scopes.c.slug == child.c[slug])
        staged = sa.exists().where(_stand_ins.c.tenant_id == child.c.tenant_id, _stand_ins.c.slug == child.c[slug])
        page = sa.select(*named).distinct().where(~held, ~staged).order_by(*named).limit(_BATCH_ROWS)
        after: sa.ColumnElement[bool] = sa.true()
        while rows := bind.execute(page.where(after)).all():
            bind.execute(
                sa.insert(_stand_ins),
                [
                    {_TENANT_COLUMN: tenant_id, "slug": name, _SCOPE_ID: _stand_in_id(tenant_id, name)}
                    for tenant_id, name in rows
                ],
            )
            after = sa.tuple_(*named) > (rows[-1][0], rows[-1][1])


def _seat_stand_ins(bind: sa.Connection) -> None:
    """Add every staged stand-in ``scopes`` does not hold yet, log how many, and drop the staging table."""
    if not sa.inspect(bind).has_table(_STAND_INS):
        return
    staged = (_stand_ins.c.tenant_id, _stand_ins.c.slug)
    held = sa.exists().where(_scopes.c.tenant_id == _stand_ins.c.tenant_id, _scopes.c.slug == _stand_ins.c.slug)
    page = sa.select(*staged, _stand_ins.c.scope_id).where(~held).order_by(*staged).limit(_BATCH_ROWS)
    seated = 0
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        bind.execute(
            sa.insert(_scopes),
            [
                {
                    _SCOPE_ID: scope_id,
                    "slug": slug,
                    _TENANT_COLUMN: tenant_id,
                    "description": "",
                    "created_at": _EPOCH,
                    "revision": 1,
                }
                for tenant_id, slug, scope_id in rows
            ],
        )
        seated += len(rows)
        after = sa.tuple_(*staged) > (rows[-1][0], rows[-1][1])
    _log.info("minted %d stand-in scopes for slugs rows named but no scope of their tenant held", seated)
    op.drop_table(_STAND_INS)


def _scope_foreign_keys(bind: sa.Connection, table: str, column: str, parent_column: str) -> list[str | None]:
    """The names of ``table``'s foreign keys from ``column`` onto ``scopes.<parent_column>`` (``None`` unnamed)."""
    return [
        fk.get("name")
        for fk in sa.inspect(bind).get_foreign_keys(table)
        if fk["referred_table"] == _SCOPES
        and fk["constrained_columns"] == [column]
        and fk["referred_columns"] == [parent_column]
    ]


def _discard_foreign_key(reflected: sa.Table, column: str) -> None:
    """Take the unnamed foreign key from ``column`` onto ``scopes`` out of the structure a copy is made from."""
    for constraint in list(reflected.foreign_key_constraints):
        if constraint.column_keys == [column] and constraint.referred_table.name == _SCOPES:
            reflected.constraints.discard(constraint)


def _rekey(reflected: sa.Table, columns: Sequence[str]) -> None:
    """Flag ``columns`` as the copy's primary key, and no others."""
    for column in reflected.columns:
        column.primary_key = column.name in columns


def _detach(bind: sa.Connection, link: _Link) -> None:
    """One batch taking the linked table off ``scopes.slug``: the slug foreign key dropped, the slug column too unless
    it stays as the label, the id required, the primary key and index moved onto the id."""
    table, slug, id_column, _, key_name, index, keeps_slug = link
    old_foreign_keys = _scope_foreign_keys(bind, table, slug, "slug")
    columns = _columns(bind, table)
    drop_slug = not keeps_slug and slug in columns
    primary_key = sa.inspect(bind).get_pk_constraint(table)
    new_key = tuple(id_column if c == slug else c for c in primary_key["constrained_columns"])
    rekey = key_name is not None and primary_key.get("name") != key_name
    indexes = _index_names(bind, table)
    drop_index = index is not None and index[0] in indexes
    create_index = index is not None and index[1] not in indexes
    if not (old_foreign_keys or drop_slug or columns[id_column] or rekey or drop_index or create_index):
        return
    _clear_stale_copy(bind, table)
    reflected = _reflected(bind, table)
    if None in old_foreign_keys:
        _discard_foreign_key(reflected, slug)
    if rekey:
        _rekey(reflected, new_key)
    with op.batch_alter_table(table, recreate="auto", copy_from=reflected) as batch:
        for name in old_foreign_keys:
            if name is not None:
                batch.drop_constraint(name, type_="foreignkey")
        if drop_index and index is not None:
            batch.drop_index(index[0])
        if rekey and key_name is not None:
            if old_name := primary_key.get("name"):
                batch.drop_constraint(old_name, type_="primary")
            batch.create_primary_key(key_name, list(new_key))
        if drop_slug:
            batch.drop_column(slug)
        if columns[id_column]:
            batch.alter_column(id_column, existing_type=sa.String(), nullable=False)
        if create_index and index is not None:
            batch.create_index(index[1], [id_column])


def _rekey_scopes(bind: sa.Connection) -> None:
    """One batch keying ``scopes`` by its id: ``pk_scopes``, the slug unique within its tenant, no tenant index."""
    inspector = sa.inspect(bind)
    primary_key = inspector.get_pk_constraint(_SCOPES)
    rekey = primary_key.get("name") != _PRIMARY_KEY
    add_unique = _TENANT_SLUG not in {u["name"] for u in inspector.get_unique_constraints(_SCOPES)}
    drop_index = _TENANT_INDEX in _index_names(bind, _SCOPES)
    columns = _columns(bind, _SCOPES)
    if not (rekey or add_unique or drop_index or columns[_SCOPE_ID] or columns["slug"]):
        return
    _clear_stale_copy(bind, _SCOPES)
    reflected = _reflected(bind, _SCOPES)
    if rekey:
        _rekey(reflected, (_SCOPE_ID,))
    with op.batch_alter_table(_SCOPES, recreate="auto", copy_from=reflected) as batch:
        if drop_index:
            batch.drop_index(_TENANT_INDEX)
        if rekey:
            if old_name := primary_key.get("name"):
                batch.drop_constraint(old_name, type_="primary")
            batch.create_primary_key(_PRIMARY_KEY, [_SCOPE_ID])
        for column in (_SCOPE_ID, "slug"):
            if columns[column]:
                batch.alter_column(column, existing_type=sa.String(), nullable=False)
        if add_unique:
            batch.create_unique_constraint(_TENANT_SLUG, [_TENANT_COLUMN, "slug"])


def _attach(bind: sa.Connection, link: _Link) -> None:
    """Add ``fk_<table>_scopes`` from the id onto ``scopes.scope_id``, unless the linked table carries it already."""
    table, _, id_column, *_ = link
    if _foreign_key(table) in _scope_foreign_keys(bind, table, id_column, _SCOPE_ID):
        return
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        batch.create_foreign_key(_foreign_key(table), _SCOPES, [id_column], [_SCOPE_ID])


def upgrade() -> None:
    bind = op.get_bind()
    _stage_stand_ins(bind)
    _mint_scope_ids(bind)
    for link in _LINKS:
        _carry_ids(bind, link)
    for link in _LINKS:
        _detach(bind, link)
    _rekey_scopes(bind)
    _seat_stand_ins(bind)
    for link in _LINKS:
        _attach(bind, link)


def _unattach(bind: sa.Connection, link: _Link) -> None:
    """Drop ``fk_<table>_scopes``, unless the linked table no longer carries it."""
    table, _, id_column, *_ = link
    if _foreign_key(table) not in _scope_foreign_keys(bind, table, id_column, _SCOPE_ID):
        return
    _clear_stale_copy(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
        batch.drop_constraint(_foreign_key(table), type_="foreignkey")


def _carry_slugs(bind: sa.Connection, link: _Link) -> None:
    """Give each row of the linked table back the slug of the scope its id names."""
    table, slug, id_column, key, key_name, *_ = link
    _add_column(bind, table, slug)
    if id_column not in _columns(bind, table):
        return
    child = _child(link, id_column)
    resolved = (
        sa.select(_scopes.c.slug)
        .where(_scopes.c.tenant_id == child.c.tenant_id, _scopes.c.scope_id == child.c[id_column])
        .correlate(child)
        .scalar_subquery()
    )
    page_key = (*key, id_column) if key_name else key
    _fill(bind, child, page_key, slug, resolved)


def _unseat_stand_ins(bind: sa.Connection) -> None:
    """Delete every stand-in scope — each one whose id is the stand-in id of its tenant and slug — a page at a time
    along the id, among the ids timestamped at the epoch."""
    if _SCOPE_ID not in _columns(bind, _SCOPES):
        return
    at_the_epoch = _scopes.c.scope_id.startswith(f"{_SCOPE_PREFIX}_{_encode(0)[:10]}", autoescape=True)
    page = (
        sa.select(_scopes.c.scope_id, _scopes.c.tenant_id, _scopes.c.slug)
        .where(at_the_epoch)
        .order_by(_scopes.c.scope_id)
        .limit(_BATCH_ROWS)
    )
    unseated = 0
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        stand_ins = [scope_id for scope_id, tenant_id, slug in rows if scope_id == _stand_in_id(tenant_id, slug)]
        if stand_ins:
            bind.execute(sa.delete(_scopes).where(_scopes.c.scope_id.in_(stand_ins)))
        unseated += len(stand_ins)
        after = _scopes.c.scope_id > rows[-1][0]
    _log.info("deleted %d stand-in scopes", unseated)


def _unkey_scopes(bind: sa.Connection) -> None:
    """One batch keying ``scopes`` by its slug again, unnamed, with its tenant index and without its id."""
    inspector = sa.inspect(bind)
    primary_key = inspector.get_pk_constraint(_SCOPES)
    rekey = primary_key["constrained_columns"] != ["slug"]
    drop_unique = _TENANT_SLUG in {u["name"] for u in inspector.get_unique_constraints(_SCOPES)}
    add_index = _TENANT_INDEX not in _index_names(bind, _SCOPES)
    drop_id = _SCOPE_ID in _columns(bind, _SCOPES)
    if not (rekey or drop_unique or add_index or drop_id):
        return
    _clear_stale_copy(bind, _SCOPES)
    reflected = _reflected(bind, _SCOPES)
    if rekey:
        # The copy keys the table by the flagged column, unnamed — the only way any dialect restores an unnamed key.
        _rekey(reflected, ("slug",))
        reflected.constraints.discard(reflected.primary_key)
    with op.batch_alter_table(_SCOPES, recreate="always" if rekey else "auto", copy_from=reflected) as batch:
        if drop_unique:
            batch.drop_constraint(_TENANT_SLUG, type_="unique")
        if drop_id:
            batch.drop_column(_SCOPE_ID)
        if add_index:
            batch.create_index(_TENANT_INDEX, [_TENANT_COLUMN])


def _reattach(bind: sa.Connection, link: _Link) -> None:
    """One batch putting the linked table back on ``scopes.slug``: the slug required, its unnamed foreign key, the
    primary key and index on it, the id dropped."""
    table, slug, id_column, _, key_name, index, _ = link
    columns = _columns(bind, table)
    restore_foreign_key = not _scope_foreign_keys(bind, table, slug, "slug")
    primary_key = sa.inspect(bind).get_pk_constraint(table)
    rekey = key_name is not None and primary_key.get("name") == key_name
    old_key = tuple(slug if c == id_column else c for c in primary_key["constrained_columns"])
    indexes = _index_names(bind, table)
    drop_index = index is not None and index[1] in indexes
    create_index = index is not None and index[0] not in indexes
    drop_id = id_column in columns
    if not (restore_foreign_key or rekey or drop_index or create_index or drop_id or columns[slug]):
        return
    _clear_stale_copy(bind, table)
    reflected = _reflected(bind, table)
    if restore_foreign_key:
        reflected.append_constraint(sa.ForeignKeyConstraint([slug], [f"{_SCOPES}.slug"]))
    if rekey:
        _rekey(reflected, old_key)
        reflected.constraints.discard(reflected.primary_key)
    # Only a copy creates an unnamed constraint, so restoring one copies the table on every dialect.
    unnamed = restore_foreign_key or rekey
    with op.batch_alter_table(table, recreate="always" if unnamed else "auto", copy_from=reflected) as batch:
        if drop_index and index is not None:
            batch.drop_index(index[1])
        if drop_id:
            batch.drop_column(id_column)
        if columns[slug]:
            batch.alter_column(slug, existing_type=sa.String(), nullable=False)
        if create_index and index is not None:
            batch.create_index(index[0], [slug])


def downgrade() -> None:
    bind = op.get_bind()
    tenants = _tenant_ids(bind)
    if len(tenants) > 1:
        raise RuntimeError(f"delete every tenant but one before downgrading; the store holds {', '.join(tenants)}")
    for link in _LINKS:
        _unattach(bind, link)
    for link in _LINKS:
        _carry_slugs(bind, link)
    _unseat_stand_ins(bind)
    _unkey_scopes(bind)
    for link in _LINKS:
        _reattach(bind, link)
