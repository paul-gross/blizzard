"""repository identity — repositories keyed by a hub-minted ``repo_`` id, a slug unique within its tenant, a free display
name, and every reference held by the id.

``repositories`` gains ``repository_id``, minted for each repository as a prefixed ULID: its timestamp the repository's
``created_at``, its random bits drawn from the repository's tenant and old name, so re-running the upgrade mints the
same ids. It gains ``slug`` too: the old name when that is a slug (lowercase letters, digits and single inner hyphens,
at most 64), otherwise the name lowercased, each run outside ``[a-z0-9]`` collapsed to one hyphen, trimmed and cut to 64
— ``repository`` when nothing is left — and, where a tenant already holds it, suffixed ``-2``, ``-3``, … Every name
that is a slug keeps it; only a derived slug is suffixed. The old name stays as the display ``name``, stripped and cut
to 128 characters where it was padded or longer, ``slug`` where nothing is left. A name that is not a slug stops
resolving once the revision is applied: it cannot be a former slug outside the slug grammar.

``repository_lifecycle_facts``, ``project_repository_links``, ``project_repository_link_facts`` and
``delivery_repo_landed`` each gain ``repository_id`` beside the name they carried, filled from the repository their own
tenant holds under that name. ``config_changes.record_key`` of the ``repository`` and ``project_repository_link`` kinds
is rewritten from the name to the id, tenant by tenant; the diffs stay as recorded, and no revision moves and no change
row is appended, since no record's configured meaning changes. A name a row carries that no repository of its tenant
holds refuses the upgrade before anything is dropped, naming the table, the tenant and the names; a landing that names
no repository keeps a null ``repository_id``.

Then, in the order every dialect accepts: the facts and links drop their foreign keys onto the repository name and
their name column, their id becoming required (the landing's stays nullable); ``project_repository_links`` is keyed
``pk_project_repository_links (project_id, repository_id)`` with its tenant index on the id; ``repositories`` is rekeyed
``pk_repositories (repository_id)`` with ``uq_repositories_tenant_slug (tenant_id, slug)``; each child gains its foreign
key onto the id. ``repository_former_slugs`` is created beside ``repositories``. Backfills page along a key in bounded
batches; every step inspects first and skips what a stopped run already did.

The downgrade restores the name keys: a repository's name is its slug again — its display name is lost, and so are its
former slugs — every name reference is filled from the repository the id names, the change rows' keys return to names,
and the ids and ``repository_former_slugs`` are dropped.
Revision ID: 20261012_0900_repository_identity
Revises: 20261011_1300_chunk_recorded_at_indexes
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261012_0900_repository_identity"
down_revision: str | None = "20261011_1300_chunk_recorded_at_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_TENANT = "tenant_id"
_REPOSITORIES = "repositories"
_FORMER_SLUGS = "repository_former_slugs"
_ID = "repository_id"
_NAME = "repository_name"
_PRIMARY_KEY = "pk_repositories"
_TENANT_SLUG = "uq_repositories_tenant_slug"
_FACTS = "repository_lifecycle_facts"
_LINKS = "project_repository_links"
_LINK_FACTS = "project_repository_link_facts"
_LANDED = "delivery_repo_landed"
_CHANGES = "config_changes"
#: The change-row kinds keyed by a repository name before this revision and by its id after.
_KEYED_KINDS = ("repository", "project_repository_link")

# The prefixed-ULID mint and the slug grammar, restated rather than imported (``bzh:frozen-revisions``): a type tag, an
# underscore, then 48 bits of millisecond timestamp and 80 further bits in Crockford base32.
_ID_PREFIX = "repo"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26
_RANDOM_BITS = 80
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_OUTSIDE_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 64
_NAME_MAX = 128
_FALLBACK_SLUG = "repository"

_repositories = sa.table(
    _REPOSITORIES,
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

# Each table naming a repository, as this revision rekeys it — frozen here, never derived from ``schema.metadata``: the
# table; the key its backfills page along; the foreign key onto the repository (or link) name it drops, ``None`` for a
# table without one; the foreign key onto the id it gains, with its target columns; whether the id stays nullable.
_Child = tuple[str, tuple[str, ...], str | None, tuple[str, str, tuple[str, ...], tuple[str, ...]] | None, bool]
_CHILDREN: tuple[_Child, ...] = (
    (
        _FACTS,
        ("id",),
        "fk_repository_lifecycle_facts_repositories",
        ("fk_repository_lifecycle_facts_repositories", _REPOSITORIES, (_ID,), (_ID,)),
        False,
    ),
    (
        _LINKS,
        ("project_id",),
        "fk_project_repository_links_repositories",
        ("fk_project_repository_links_repositories", _REPOSITORIES, (_ID,), (_ID,)),
        False,
    ),
    (
        _LINK_FACTS,
        ("id",),
        "fk_project_repository_link_facts_links",
        (
            "fk_project_repository_link_facts_links",
            _LINKS,
            ("project_id", _ID),
            ("project_id", _ID),
        ),
        False,
    ),
    (_LANDED, ("id",), None, None, True),
)
#: The tables whose name column is ``name`` rather than ``repository_name``.
_NAME_COLUMN = {_FACTS: "name"}

_LINKS_INDEX = "ix_project_repository_links_tenant_repository"
_LINK_FACTS_INDEX = "ix_project_repository_link_facts_tenant_link"


def _name_column(table: str) -> str:
    return _NAME_COLUMN.get(table, _NAME)


def _encode(value: int) -> str:
    chars: list[str] = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _drawn(key: str) -> int:
    """``_RANDOM_BITS`` bits drawn from ``key`` — the same on every run."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[: _RANDOM_BITS // 8], "big")


def _minted(tenant_id: str, name: str, created_at: datetime) -> str:
    """The id of the repository ``tenant_id`` holds under ``name``: timestamped at its creation, the same every run."""
    at = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    millis = max(0, (at - _EPOCH) // timedelta(milliseconds=1))
    return f"{_ID_PREFIX}_{_encode((millis << _RANDOM_BITS) | _drawn(f'{tenant_id}/{name}'))}"


def _derived(name: str) -> str:
    """The slug closest to ``name``: lowercased, each run outside ``[a-z0-9]`` one hyphen, trimmed, cut to the bound."""
    derived = _OUTSIDE_SLUG.sub("-", name.lower()).strip("-")[:_SLUG_MAX].strip("-")
    return derived or _FALLBACK_SLUG


def _suffixed(base: str, taken: set[str]) -> str:
    """``base``, or the first ``base-2``, ``base-3``, … no repository of the tenant holds, cut to the bound."""
    if base not in taken:
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
        sa.PrimaryKeyConstraint(_TENANT, "slug", name="pk_repository_former_slugs"),
        sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name="fk_repository_former_slugs_tenant_id"),
    )


def _mint(bind: sa.Connection) -> None:
    """Give every repository still without them its id and slug, tenant by tenant, a page at a time along
    ``(tenant_id, name)``. A name that is a slug keeps it; a derived slug avoids every slug its tenant holds."""
    _add_column(bind, _REPOSITORIES, _ID)
    _add_column(bind, _REPOSITORIES, "slug")
    r = _repositories
    taken: dict[str, set[str]] = {}
    for tenant_id, slug in bind.execute(sa.select(r.c.tenant_id, r.c.slug).where(r.c.slug.is_not(None))):
        taken.setdefault(tenant_id, set()).add(slug)
    key = sa.tuple_(r.c.tenant_id, r.c.name)
    page = (
        sa.select(r.c.tenant_id, r.c.name, r.c.created_at, r.c.slug)
        .where(sa.or_(r.c.repository_id.is_(None), r.c.slug.is_(None)))
        .order_by(r.c.tenant_id, r.c.name)
        .limit(_BATCH_ROWS)
    )
    stamp = (
        sa.update(r)
        .where(r.c.tenant_id == sa.bindparam("b_tenant"), r.c.name == sa.bindparam("b_name"))
        .values({_ID: sa.bindparam("b_id"), "slug": sa.bindparam("b_slug")})
    )
    # Names that are slugs claim theirs before any derived slug looks for a free one.
    for tenant_id, name in bind.execute(
        sa.select(r.c.tenant_id, r.c.name).where(r.c.slug.is_(None)).order_by(r.c.tenant_id, r.c.name)
    ):
        if len(name) <= _SLUG_MAX and _SLUG.match(name):
            taken.setdefault(tenant_id, set()).add(name)
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        stamps: list[dict[str, Any]] = []
        for tenant_id, name, created_at, slug in rows:
            held = taken.setdefault(tenant_id, set())
            if slug is None:
                if len(name) <= _SLUG_MAX and _SLUG.match(name):
                    slug = name
                else:
                    slug = _suffixed(_derived(name), held)
                    held.add(slug)
            stamps.append(
                {"b_tenant": tenant_id, "b_name": name, "b_id": _minted(tenant_id, name, created_at), "b_slug": slug}
            )
        bind.execute(stamp, stamps)
        after = key > (rows[-1][0], rows[-1][1])


def _carry_ids(bind: sa.Connection, table: str, key: tuple[str, ...]) -> None:
    """Give each row of ``table`` the id of the repository its own tenant holds under the name it carries."""
    _add_column(bind, table, _ID)
    name = _name_column(table)
    if name not in _columns(bind, table):
        return
    child = sa.table(table, *(sa.column(c, sa.String) for c in dict.fromkeys((*key, name, _ID, _TENANT))))
    held = (
        sa.select(_repositories.c.repository_id)
        .where(_repositories.c.tenant_id == child.c.tenant_id, _repositories.c.name == child.c[name])
        .correlate(child)
        .scalar_subquery()
    )
    _fill(bind, child, (*key, name) if table == _LINKS else key, _ID, held)


def _carry_change_keys(bind: sa.Connection) -> None:
    """Rewrite the key of every repository and link change row from the repository's name to its id, tenant by tenant,
    along the row id; a key that is already an id of the tenant is left."""
    c, r = _changes, _repositories
    named = sa.select(r.c.repository_id).where(r.c.tenant_id == c.c.tenant_id, r.c.name == c.c.record_key)
    held = sa.exists().where(r.c.tenant_id == c.c.tenant_id, r.c.repository_id == c.c.record_key)
    _rewrite_keys(bind, [c.c.record_kind.in_(_KEYED_KINDS), ~held, sa.exists(named)], named)


def _rewrite_keys(bind: sa.Connection, conditions: list[sa.ColumnElement[bool]], value: sa.Select[Any]) -> None:
    """Set the key of the change rows ``conditions`` select to ``value``, a page of row ids at a time."""
    c = _changes
    page = sa.select(c.c.id).where(*conditions).order_by(c.c.id).limit(_BATCH_ROWS)
    last = 0
    while rows := bind.execute(page.where(c.c.id > last)).scalars().all():
        bind.execute(
            sa.update(c)
            .where(*conditions, c.c.id > last, c.c.id <= rows[-1])
            .values(record_key=value.correlate(c).scalar_subquery())
        )
        last = rows[-1]


def _refuse_dangling(bind: sa.Connection) -> None:
    """Refuse — before anything is dropped — a name a row carries that no repository of its tenant holds."""
    dangling: list[str] = []
    for table, key, *_ in _CHILDREN:
        name = _name_column(table)
        if name not in _columns(bind, table):
            continue
        child = sa.table(table, sa.column(name, sa.String), sa.column(_ID, sa.String), sa.column(_TENANT, sa.String))
        rows = bind.execute(
            sa.select(child.c.tenant_id, child.c[name])
            .where(child.c[name].is_not(None), child.c.repository_id.is_(None))
            .distinct()
            .order_by(child.c.tenant_id, child.c[name])
            .limit(5)
        ).all()
        dangling.extend(f"{table}: tenant {tenant} names repository {value!r}" for tenant, value in rows)
    if dangling:
        raise RuntimeError(
            "a repository name no repository of its tenant holds cannot become an id; "
            + "; ".join(dangling)
            + " — create the repository, or remove the row, then upgrade again"
        )


def _normalize_names(bind: sa.Connection) -> None:
    """Make every display name a valid one: stripped and cut to the bound where padded or longer."""
    r = _repositories
    page = (
        sa.select(r.c.repository_id, r.c.name, r.c.slug)
        .where(sa.or_(sa.func.length(r.c.name) > _NAME_MAX, r.c.name != sa.func.trim(r.c.name), r.c.name == ""))
        .order_by(r.c.repository_id)
        .limit(_BATCH_ROWS)
    )
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        for repository_id, name, slug in rows:
            bind.execute(sa.update(r).where(r.c.repository_id == repository_id).values(name=_display(name, slug)))
        after = r.c.repository_id > rows[-1][0]


def _detach(bind: sa.Connection, child: _Child) -> None:
    """One batch taking ``table`` off the repository name: the foreign key onto it dropped, the name column too, the id
    required (unless it stays nullable), and a table's key and index moved onto the id."""
    table, _, old_foreign_key, _, nullable = child
    name = _name_column(table)
    columns = _columns(bind, table)
    drop_foreign_key = old_foreign_key is not None and old_foreign_key in _foreign_keys(bind, table)
    drop_name = name in columns
    tighten = not nullable and columns[_ID]
    indexes = _index_names(bind, table)
    index = {_LINKS: _LINKS_INDEX, _LINK_FACTS: _LINK_FACTS_INDEX}.get(table)
    drop_index = index is not None and index in indexes and drop_name
    primary_key = sa.inspect(bind).get_pk_constraint(table)
    rekey = table == _LINKS and primary_key["constrained_columns"] != ["project_id", _ID]
    if not (drop_foreign_key or drop_name or tighten or drop_index or rekey):
        return
    _clear_stale_copy(bind, table)
    reflected = _reflected(bind, table)
    if rekey and not primary_key.get("name"):
        _rekey(reflected, ("project_id", _ID))
    with op.batch_alter_table(table, recreate="auto", copy_from=reflected) as batch:
        if drop_foreign_key and old_foreign_key is not None:
            batch.drop_constraint(old_foreign_key, type_="foreignkey")
        if drop_index and index is not None:
            batch.drop_index(index)
        if rekey:
            if old_name := primary_key.get("name"):
                batch.drop_constraint(old_name, type_="primary")
            batch.create_primary_key("pk_project_repository_links", ["project_id", _ID])
        if drop_name:
            batch.drop_column(name)
        if tighten:
            batch.alter_column(_ID, existing_type=sa.String(), nullable=False)
        if drop_index and index is not None:
            batch.create_index(
                index,
                [_TENANT, _ID] if table == _LINKS else [_TENANT, "project_id", _ID, "id"],
            )


def _rekey_repositories(bind: sa.Connection) -> None:
    """One batch keying ``repositories`` by its id: ``pk_repositories``, the slug unique within its tenant."""
    inspector = sa.inspect(bind)
    primary_key = inspector.get_pk_constraint(_REPOSITORIES)
    rekey = primary_key.get("name") != _PRIMARY_KEY or primary_key["constrained_columns"] != [_ID]
    add_unique = _TENANT_SLUG not in {u["name"] for u in inspector.get_unique_constraints(_REPOSITORIES)}
    columns = _columns(bind, _REPOSITORIES)
    if not (rekey or add_unique or columns[_ID] or columns["slug"]):
        return
    _clear_stale_copy(bind, _REPOSITORIES)
    reflected = _reflected(bind, _REPOSITORIES)
    if rekey:
        _rekey(reflected, (_ID,))
    with op.batch_alter_table(_REPOSITORIES, recreate="auto", copy_from=reflected) as batch:
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
    """Add ``fk_repository_former_slugs_repositories`` once ``repositories`` is keyed by its id."""
    name = "fk_repository_former_slugs_repositories"
    if name in _foreign_keys(bind, _FORMER_SLUGS):
        return
    with op.batch_alter_table(_FORMER_SLUGS, recreate="auto", copy_from=_reflected(bind, _FORMER_SLUGS)) as batch:
        batch.create_foreign_key(name, _REPOSITORIES, [_ID], [_ID])


def _carry(bind: sa.Connection) -> None:
    """Every reference carries the id beside the name; the dangling are refused; the display names are made valid."""
    for table, key, *_ in _CHILDREN:
        _carry_ids(bind, table, key)
    _carry_change_keys(bind)
    _refuse_dangling(bind)
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
UPGRADE_STEPS = (_create_former_slugs, _mint, _carry, _detach_all, _rekey_repositories, _attach_all)


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


def _carry_names(bind: sa.Connection, table: str, key: tuple[str, ...]) -> None:
    """Give each row of ``table`` back the slug of the repository its id names, as its name."""
    name = _name_column(table)
    _add_column(bind, table, name)
    if _ID not in _columns(bind, table):
        return
    child = sa.table(table, *(sa.column(c, sa.String) for c in dict.fromkeys((*key, name, _ID, _TENANT))))
    resolved = (
        sa.select(_repositories.c.slug)
        .where(_repositories.c.repository_id == child.c[_ID])
        .correlate(child)
        .scalar_subquery()
    )
    _fill(bind, child, (*key, _ID) if table == _LINKS else key, name, resolved)


def _restore_change_keys(bind: sa.Connection) -> None:
    """Rewrite every repository and link change row's key from the repository's id back to its slug."""
    c, r = _changes, _repositories
    slug = sa.select(r.c.slug).where(r.c.tenant_id == c.c.tenant_id, r.c.repository_id == c.c.record_key)
    _rewrite_keys(bind, [c.c.record_kind.in_(_KEYED_KINDS), sa.exists(slug)], slug)


def _reattach(bind: sa.Connection, child: _Child) -> None:
    """One batch putting ``table`` back on the repository name: the name required (the landing's stays nullable), the
    key and index on it, the id dropped."""
    table, *_ = child
    name = _name_column(table)
    columns = _columns(bind, table)
    index = {_LINKS: _LINKS_INDEX, _LINK_FACTS: _LINK_FACTS_INDEX}.get(table)
    primary_key = sa.inspect(bind).get_pk_constraint(table)
    rekey = table == _LINKS and primary_key["constrained_columns"] != ["project_id", name]
    drop_id = _ID in columns
    tighten = table != _LANDED and columns[name]
    if not (rekey or drop_id or tighten):
        return
    _clear_stale_copy(bind, table)
    reflected = _reflected(bind, table)
    if rekey and not primary_key.get("name"):
        _rekey(reflected, ("project_id", name))
    drop_index = index is not None and index in _index_names(bind, table) and drop_id
    with op.batch_alter_table(table, recreate="auto", copy_from=reflected) as batch:
        if drop_index and index is not None:
            batch.drop_index(index)
        if rekey:
            if old_name := primary_key.get("name"):
                batch.drop_constraint(old_name, type_="primary")
            batch.create_primary_key("pk_project_repository_links", ["project_id", name])
        if drop_id:
            batch.drop_column(_ID)
        if tighten:
            batch.alter_column(name, existing_type=sa.String(), nullable=False)
        if drop_index and index is not None:
            batch.create_index(index, [_TENANT, name] if table == _LINKS else [_TENANT, "project_id", name, "id"])


def _unkey_repositories(bind: sa.Connection) -> None:
    """One batch keying ``repositories`` by ``(tenant_id, name)`` again, its name its slug, without the id and slug."""
    r = _repositories
    bind.execute(sa.update(r).values(name=r.c.slug))
    inspector = sa.inspect(bind)
    primary_key = inspector.get_pk_constraint(_REPOSITORIES)
    rekey = primary_key["constrained_columns"] != [_TENANT, "name"]
    drop_unique = _TENANT_SLUG in {u["name"] for u in inspector.get_unique_constraints(_REPOSITORIES)}
    columns = _columns(bind, _REPOSITORIES)
    drop = [c for c in (_ID, "slug") if c in columns]
    if not (rekey or drop_unique or drop):
        return
    _clear_stale_copy(bind, _REPOSITORIES)
    reflected = _reflected(bind, _REPOSITORIES)
    if rekey:
        _rekey(reflected, (_TENANT, "name"))
    with op.batch_alter_table(_REPOSITORIES, recreate="auto", copy_from=reflected) as batch:
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
            "fk_repository_lifecycle_facts_repositories",
            _REPOSITORIES,
            [_TENANT, "name"],
            [_TENANT, "name"],
        )
    elif table == _LINKS:
        name, target, local, remote = (
            "fk_project_repository_links_repositories",
            _REPOSITORIES,
            [_TENANT, _NAME],
            [_TENANT, "name"],
        )
    elif table == _LINK_FACTS:
        name, target, local, remote = (
            "fk_project_repository_link_facts_links",
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
    for table, key, *_ in _CHILDREN:
        _carry_names(bind, table, key)
    _restore_change_keys(bind)
    for child in _CHILDREN:
        _reattach(bind, child)
    _unkey_repositories(bind)
    for table, *_ in _CHILDREN:
        _name_foreign_key(bind, table)
