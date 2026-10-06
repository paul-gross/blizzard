"""runner_minted_ids — every runner is keyed by a hub-minted ``rn_`` id; its old id becomes its display name.

Re-running the upgrade mints the same ids, so an id a runner already learned stays valid; the downgrade is lossy.
Revision ID: 20261006_1200_runner_minted_ids
Revises: 20261006_0900_config_import_facts
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261006_1200_runner_minted_ids"
down_revision: str | None = "20261006_0900_config_import_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REGISTRATIONS = "runner_registrations"

# Restated, not imported, from ``blizzard.foundation.ids`` (``bzh:frozen-revisions``).
_RUNNER_PREFIX = "rn"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26  # 48 bits of millisecond timestamp, then 80 random bits
_RANDOM_BITS = 80
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# The hub's own executor id on lease and transition rows — restated from ``blizzard.hub.delivery.hub_node``.
_HUB_RUNNER_ID = "hub"
_HUB_EXECUTOR_TABLES = frozenset({"lease_facts", "transitions"})

# ``WorkItemAuthorKind.FLEET``, restated: the author kind whose payload names a runner.
_FLEET_AUTHOR_KIND = "fleet"

# Every other column holding a runner id; the first three reference ``runner_registrations`` by foreign key.
_RUNNER_ID_COLUMNS: tuple[tuple[str, str], ...] = (
    ("runner_pause_facts", "runner_id"),
    ("runner_lifecycle_facts", "runner_id"),
    ("runner_token_revocations", "runner_id"),
    ("runner_local_pause_facts", "runner_id"),
    ("runner_external_usage", "runner_id"),
    ("runner_external_usage_misses", "runner_id"),
    ("runner_high_water", "runner_id"),
    ("transcript_high_water", "runner_id"),
    ("lease_facts", "runner_id"),
    ("epoch_owners", "runner_id"),
    ("route_created", "runner_id"),
    ("transitions", "runner_id"),
    ("usage_facts", "runner_id"),
    ("questions", "runner_id"),
    ("decisions", "imposed_by_runner_id"),
    ("event_log", "runner_id"),
    ("transcript_segments", "runner_id"),
    ("work_item_proposals", "runner_id"),
)

# Frozen table and read stubs (``bzh:frozen-revisions``).
_frozen = sa.MetaData()
_registrations = sa.Table(
    _REGISTRATIONS,
    _frozen,
    sa.Column("runner_id", sa.String, primary_key=True),
    sa.Column("workspace_id", sa.String),
    sa.Column("registered_at", UtcDateTime),
    sa.Column("last_seen_at", UtcDateTime),
    sa.Column("token_hash", sa.Text),
    sa.Column("env_capacity", sa.Integer),
    sa.Column("public_url", sa.Text),
    sa.Column("redirect_uris", sa.Text),
    sa.Column("capabilities", sa.Text),
    sa.Column("subscriptions", sa.Text),
    sa.Column("gates", sa.Text),
    sa.Column("name", sa.String),
    sa.Column("added_at", UtcDateTime),
    sa.Column("added_by", sa.String),
)
_references = {
    (table, column): sa.Table(table, _frozen, sa.Column(column, sa.String)) for table, column in _RUNNER_ID_COLUMNS
}
_work_items = sa.Table(
    "work_items",
    _frozen,
    sa.Column("work_item_id", sa.String, primary_key=True),
    sa.Column("author_kind", sa.String),
    sa.Column("author_payload", sa.Text),
)


def _columns(bind: sa.Connection) -> dict[str, dict[str, object]]:
    return {str(c["name"]): dict(c) for c in sa.inspect(bind).get_columns(_REGISTRATIONS)}


def _reshaped(bind: sa.Connection) -> bool:
    name = _columns(bind).get("name")
    return name is not None and not name["nullable"]


def _encode(value: int) -> str:
    chars = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _minted_ids(bind: sa.Connection) -> dict[str, str]:
    """Every registration still keyed by its old id (its ``name`` not yet set), mapped to its minted id."""
    rows = bind.execute(
        sa.select(_registrations.c.runner_id, _registrations.c.registered_at)
        .where(_registrations.c.name.is_(None))
        .order_by(_registrations.c.registered_at, _registrations.c.runner_id)
    ).all()
    minted: dict[str, str] = {}
    previous = -1
    for old_id, registered_at in rows:
        millis = (registered_at - _EPOCH) // timedelta(milliseconds=1)
        randomness = int.from_bytes(hashlib.sha256(old_id.encode()).digest()[: _RANDOM_BITS // 8], "big")
        previous = max((millis << _RANDOM_BITS) | randomness, previous + 1)
        minted[old_id] = f"{_RUNNER_PREFIX}_{_encode(previous)}"
    return minted


def _restored_ids(bind: sa.Connection) -> dict[str, str]:
    """Every registration whose name maps back to an id of its own, mapped to that name."""
    rows = bind.execute(
        sa.select(_registrations.c.runner_id, _registrations.c.name).order_by(
            _registrations.c.added_at, _registrations.c.runner_id
        )
    ).all()
    ids = {runner_id for runner_id, _name in rows}
    holders = Counter(name for _runner_id, name in rows)
    return {runner_id: name for runner_id, name in rows if name != runner_id and holders[name] == 1 and name not in ids}


def _rekey(bind: sa.Connection, mapping: Mapping[str, str], *, name_after_old_id: bool) -> None:
    """Move each registration in ``mapping`` from its old id to its new one: copy the row under the new
    id, repoint every reference, then delete the old row. ``name_after_old_id`` also names each copy
    after the id it leaves and stamps it added when it first registered."""
    held = set(
        bind.execute(
            sa.select(_registrations.c.runner_id).where(_registrations.c.runner_id.in_(list(mapping.values())))
        ).scalars()
    )
    for old_id, new_id in mapping.items():
        if new_id in held:
            continue
        row = bind.execute(sa.select(_registrations).where(_registrations.c.runner_id == old_id)).mappings().one()
        copy = {**row, "runner_id": new_id}
        if name_after_old_id:
            copy |= {"name": old_id, "added_at": row["registered_at"]}
        bind.execute(_registrations.insert().values(copy))
    for (table, column), stub in _references.items():
        keyed = {
            old_id: new_id
            for old_id, new_id in mapping.items()
            if not (table in _HUB_EXECUTOR_TABLES and old_id == _HUB_RUNNER_ID)
        }
        if keyed:
            reference = stub.c[column]
            bind.execute(
                stub.update().where(reference.in_(list(keyed))).values({column: sa.case(keyed, value=reference)})
            )
    _rekey_fleet_authors(bind, mapping)
    bind.execute(_registrations.delete().where(_registrations.c.runner_id.in_(list(mapping))))


def _rekey_fleet_authors(bind: sa.Connection, mapping: Mapping[str, str]) -> None:
    """Repoint the runner a fleet-authored work item's JSON ``author_payload`` names, its key order kept."""
    rows = bind.execute(
        sa.select(_work_items.c.work_item_id, _work_items.c.author_payload)
        .where(_work_items.c.author_kind == _FLEET_AUTHOR_KIND)
        .order_by(_work_items.c.work_item_id)
    ).all()
    for work_item_id, raw in rows:
        payload = json.loads(raw)
        runner_id = payload.get("runner_id") if isinstance(payload, dict) else None
        if isinstance(runner_id, str) and runner_id in mapping:
            payload["runner_id"] = mapping[runner_id]
            bind.execute(
                _work_items.update()
                .where(_work_items.c.work_item_id == work_item_id)
                .values(author_payload=json.dumps(payload))
            )


def upgrade() -> None:
    bind = op.get_bind()
    if _reshaped(bind):
        return  # already keyed by minted ids — this revision's own guard, not per-row

    present = _columns(bind)
    for column in (
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("added_at", UtcDateTime(), nullable=True),
        sa.Column("added_by", sa.String(), nullable=True),
    ):
        if column.name not in present:
            op.add_column(_REGISTRATIONS, column)

    minted = _minted_ids(bind)
    if minted:
        _rekey(bind, minted, name_after_old_id=True)

    # Default `recreate="auto"` (`bzh:sql-portable`): sqlite copies the table to change nullability,
    # while postgres alters in place — never a copy of a table three foreign keys reference.
    with op.batch_alter_table(_REGISTRATIONS) as batch:
        batch.alter_column("name", existing_type=sa.String(), nullable=False)
        batch.alter_column("added_at", existing_type=UtcDateTime(), nullable=False)
        batch.alter_column("workspace_id", existing_type=sa.String(), nullable=True)
        batch.alter_column("registered_at", existing_type=UtcDateTime(), nullable=True)
        batch.alter_column("last_seen_at", existing_type=UtcDateTime(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if "name" not in _columns(bind):
        return  # already the pre-reshape shape

    # A runner added but never connected has none of these: it reads as registered when it was added.
    for column, value in (
        (_registrations.c.registered_at, _registrations.c.added_at),
        (_registrations.c.last_seen_at, _registrations.c.added_at),
        (_registrations.c.workspace_id, ""),
    ):
        bind.execute(_registrations.update().where(column.is_(None)).values({column.name: value}))
    restored = _restored_ids(bind)
    if restored:
        _rekey(bind, restored, name_after_old_id=False)

    with op.batch_alter_table(_REGISTRATIONS) as batch:
        batch.drop_column("added_by")
        batch.drop_column("added_at")
        batch.drop_column("name")
        batch.alter_column("workspace_id", existing_type=sa.String(), nullable=False)
        batch.alter_column("registered_at", existing_type=UtcDateTime(), nullable=False)
        batch.alter_column("last_seen_at", existing_type=UtcDateTime(), nullable=False)
