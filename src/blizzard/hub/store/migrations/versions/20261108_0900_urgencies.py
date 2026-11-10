"""urgencies — every tenant's ordered urgency set, its settings naming the default, and every chunk's urgency.

Creates `urgencies`, `urgency_lifecycle_facts`, `urgency_positions`, and `tenant_settings`, each tenant-keyed with a
tenant-led index. Then, for every tenant: an urgency with slug and name `standard`, color `slate`, no mark, created by
`migration`, revision 1, at position `0`, with its `create` change row from the `migration` door; and the tenant's
settings, `default_urgency` naming it, revision 1, with their `create` change row. Neither change row names a project.
The urgency's `urg_` id is timestamped at this revision's instant, `2026-11-08T09:00:00Z`, its 80 random bits the first
ten bytes of the SHA-256 of `urgency/<tenant_id>/standard`, so a re-run mints the same id.

`chunks` gains a nullable `urgency_id` with no foreign key, and the tenant-led `ix_chunks_tenant_urgency_id`; every chunk,
finished or open, is given its tenant's `standard`, in pages of 1000 rows. Every step inspects first and skips what a
stopped run already did.

The downgrade drops the index and the column, the change rows of the three new record kinds, and the four tables.
Revision ID: 20261108_0900_urgencies
Revises: 20261106_0900_errand_marks
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.migrations import migration_clock
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261108_0900_urgencies"
down_revision: str | None = "20261106_0900_errand_marks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_TENANT = "tenant_id"
_URGENCIES = "urgencies"
_FACTS = "urgency_lifecycle_facts"
_POSITIONS = "urgency_positions"
_SETTINGS = "tenant_settings"
_CHUNKS = "chunks"
_COLUMN = "urgency_id"
_CHUNK_INDEX = "ix_chunks_tenant_urgency_id"
_MIGRATION = "migration"
_SLUG = "standard"
_COLOR = "slate"
_POSITION = 0.0
_KINDS = ("urgency", "urgency_order", "tenant_settings")

# --- the mint, frozen -------------------------------------------------------------------------------------------------

_ID_PREFIX = "urg"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26
_RANDOM_BITS = 80
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_INSTANT = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)

_tenants = sa.table("tenants", sa.column(_TENANT, sa.String))
_urgencies = sa.table(
    _URGENCIES,
    sa.column(_COLUMN, sa.String),
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column("color", sa.String),
    sa.column("created_at", UtcDateTime),
    sa.column("created_by", sa.String),
    sa.column("revision", sa.Integer),
    sa.column(_TENANT, sa.String),
)
_positions = sa.table(
    _POSITIONS,
    sa.column(_COLUMN, sa.String),
    sa.column("position", sa.Float),
    sa.column("set_at", UtcDateTime),
    sa.column("set_by", sa.String),
    sa.column(_TENANT, sa.String),
)
_settings = sa.table(
    _SETTINGS,
    sa.column(_TENANT, sa.String),
    sa.column("default_urgency_id", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("updated_at", UtcDateTime),
    sa.column("updated_by", sa.String),
)
_chunks = sa.table(
    _CHUNKS, sa.column("chunk_id", sa.String), sa.column(_COLUMN, sa.String), sa.column(_TENANT, sa.String)
)
_changes = sa.table(
    "config_changes",
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
    sa.column(_TENANT, sa.String),
    sa.column("project_id", sa.String),
)


def _encode(value: int) -> str:
    chars: list[str] = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _drawn(key: str) -> int:
    """``_RANDOM_BITS`` bits drawn from ``key`` — the same on every run."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[: _RANDOM_BITS // 8], "big")


def _minted(tenant_id: str) -> str:
    """The id of ``tenant_id``'s ``standard``: timestamped at this revision's instant, the same every run."""
    millis = (_INSTANT - _EPOCH) // timedelta(milliseconds=1)
    return f"{_ID_PREFIX}_{_encode((millis << _RANDOM_BITS) | _drawn(f'urgency/{tenant_id}/{_SLUG}'))}"


# --- steps ------------------------------------------------------------------------------------------------------------


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name=f"fk_{table}_tenant_id")


def _has(bind: sa.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _indexes(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def _create_tables(bind: sa.Connection) -> None:
    if not _has(bind, _URGENCIES):
        op.create_table(
            _URGENCIES,
            sa.Column(_COLUMN, sa.String(), primary_key=True),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("color", sa.String(), nullable=False),
            sa.Column("emoji", sa.String(), nullable=True),
            sa.Column("icon_media_type", sa.String(), nullable=True),
            sa.Column("icon_data", sa.LargeBinary(), nullable=True),
            sa.Column("icon_digest", sa.String(), nullable=True),
            sa.Column("created_at", UtcDateTime(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(_TENANT, sa.String(), nullable=False),
            _tenant_fk(_URGENCIES),
            sa.UniqueConstraint(_TENANT, "slug", name="uq_urgencies_tenant_slug"),
        )
    for table in (_FACTS, _POSITIONS):
        if _has(bind, table):
            continue
        payload = (
            [sa.Column("retired", sa.Boolean(), nullable=False)]
            if table == _FACTS
            else [sa.Column("position", sa.Float(), nullable=False)]
        )
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(_COLUMN, sa.String(), nullable=False),
            *payload,
            sa.Column("set_at", UtcDateTime(), nullable=False),
            sa.Column("set_by", sa.String(), nullable=False),
            sa.Column(_TENANT, sa.String(), nullable=False),
            sa.ForeignKeyConstraint([_COLUMN], ["urgencies.urgency_id"], name=f"fk_{table}_urgencies"),
            _tenant_fk(table),
        )
    for table in (_FACTS, _POSITIONS):
        name = f"ix_{table}_tenant_urgency_id"
        if name not in _indexes(bind, table):
            op.create_index(name, table, [_TENANT, _COLUMN])
    if not _has(bind, _SETTINGS):
        op.create_table(
            _SETTINGS,
            sa.Column(_TENANT, sa.String(), primary_key=True),
            sa.Column("default_urgency_id", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", UtcDateTime(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=False),
            _tenant_fk(_SETTINGS),
            sa.ForeignKeyConstraint(
                ["default_urgency_id"], ["urgencies.urgency_id"], name="fk_tenant_settings_urgencies"
            ),
        )


def _change(at: datetime, kind: str, key: str, diff: list[dict[str, object]], tenant_id: str) -> dict[str, object]:
    return {
        "recorded_at": at,
        "actor": _MIGRATION,
        "door": _MIGRATION,
        "record_kind": kind,
        "record_key": key,
        "revision": 1,
        "op": "create",
        "diff": json.dumps(diff),
        "apply_id": None,
        _TENANT: tenant_id,
        "project_id": None,
    }


def _recorded(bind: sa.Connection, kind: str) -> set[str]:
    return set(bind.execute(sa.select(_changes.c.record_key).where(_changes.c.record_kind == kind)).scalars())


def _seed_urgencies(bind: sa.Connection) -> None:
    """Give each tenant its ``standard``, its position fact, and its change row, each not yet written."""
    at = migration_clock().now()
    tenants = list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())
    held = set(bind.execute(sa.select(_urgencies.c.urgency_id)).scalars())
    placed = set(bind.execute(sa.select(_positions.c.urgency_id)).scalars())
    recorded = _recorded(bind, "urgency")
    for tenant_id in tenants:
        urgency_id = _minted(tenant_id)
        if urgency_id not in held:
            bind.execute(
                sa.insert(_urgencies).values(
                    urgency_id=urgency_id,
                    slug=_SLUG,
                    name=_SLUG,
                    color=_COLOR,
                    created_at=at,
                    created_by=_MIGRATION,
                    revision=1,
                    tenant_id=tenant_id,
                )
            )
        if urgency_id not in placed:
            bind.execute(
                sa.insert(_positions).values(
                    urgency_id=urgency_id, position=_POSITION, set_at=at, set_by=_MIGRATION, tenant_id=tenant_id
                )
            )
        if urgency_id not in recorded:
            diff: list[dict[str, object]] = [
                {"field": "slug", "old": None, "new": _SLUG},
                {"field": "name", "old": None, "new": _SLUG},
                {"field": "color", "old": None, "new": _COLOR},
            ]
            bind.execute(sa.insert(_changes).values(_change(at, "urgency", urgency_id, diff, tenant_id)))


def _seed_settings(bind: sa.Connection) -> None:
    """Give each tenant its settings naming its ``standard``, and their change row, each not yet written."""
    at = migration_clock().now()
    tenants = list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())
    held = set(bind.execute(sa.select(_settings.c.tenant_id)).scalars())
    recorded = _recorded(bind, "tenant_settings")
    for tenant_id in tenants:
        if tenant_id not in held:
            bind.execute(
                sa.insert(_settings).values(
                    tenant_id=tenant_id,
                    default_urgency_id=_minted(tenant_id),
                    revision=1,
                    updated_at=at,
                    updated_by=_MIGRATION,
                )
            )
        if tenant_id not in recorded:
            diff: list[dict[str, object]] = [{"field": "default_urgency", "old": None, "new": _SLUG}]
            bind.execute(sa.insert(_changes).values(_change(at, "tenant_settings", tenant_id, diff, tenant_id)))


def _add_column(bind: sa.Connection) -> None:
    if _COLUMN not in {str(c["name"]) for c in sa.inspect(bind).get_columns(_CHUNKS)}:
        op.add_column(_CHUNKS, sa.Column(_COLUMN, sa.String(), nullable=True))
    if _CHUNK_INDEX not in _indexes(bind, _CHUNKS):
        op.create_index(_CHUNK_INDEX, _CHUNKS, [_TENANT, _COLUMN])


def _fill_chunks(bind: sa.Connection) -> None:
    """Give every chunk still carrying no urgency its tenant's ``standard``, a page of rows at a time."""
    tenants = list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())
    for tenant_id in tenants:
        urgency_id = _minted(tenant_id)
        while True:
            page = list(
                bind.execute(
                    sa.select(_chunks.c.chunk_id)
                    .where(_chunks.c.tenant_id == tenant_id, _chunks.c.urgency_id.is_(None))
                    .order_by(_chunks.c.chunk_id)
                    .limit(_BATCH_ROWS)
                ).scalars()
            )
            if not page:
                break
            bind.execute(sa.update(_chunks).where(_chunks.c.chunk_id.in_(page)).values(urgency_id=urgency_id))


#: In the order the upgrade runs them; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS = (_create_tables, _seed_urgencies, _seed_settings, _add_column, _fill_chunks)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def downgrade() -> None:
    bind = op.get_bind()
    if _CHUNK_INDEX in _indexes(bind, _CHUNKS):
        op.drop_index(_CHUNK_INDEX, table_name=_CHUNKS)
    if _COLUMN in {str(c["name"]) for c in sa.inspect(bind).get_columns(_CHUNKS)}:
        _clear_stale_copy(bind, _CHUNKS)
        reflected = sa.Table(_CHUNKS, sa.MetaData(), autoload_with=bind)
        with op.batch_alter_table(_CHUNKS, recreate="auto", copy_from=reflected) as batch:
            batch.drop_column(_COLUMN)
    bind.execute(sa.delete(_changes).where(_changes.c.record_kind.in_(_KINDS)))
    for table in (_SETTINGS, _POSITIONS, _FACTS, _URGENCIES):
        if _has(bind, table):
            op.drop_table(table)
