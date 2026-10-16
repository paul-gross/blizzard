"""Helpers the ``runner_store_migrations`` tests share across the revisions they prove."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.store.utc import UtcDateTime

_NEW = datetime(2026, 10, 1, 12, tzinfo=UTC)


_LATER = datetime(2026, 10, 6, 12, tzinfo=UTC)


# Each keyed table's shape at the identity revision's parent — only the columns these tests write.
_keyed = sa.MetaData()


_leases = sa.Table(
    "leases",
    _keyed,
    sa.Column("lease_id", sa.String(), primary_key=True),
    sa.Column("chunk_id", sa.String()),
    sa.Column("epoch", sa.Integer()),
    sa.Column("runner_id", sa.String()),
    sa.Column("created_at", UtcDateTime()),
)


# The revision before the store is scoped by runner: its upgrade stamps every row with the runner the identity row
# names, so a store meant to be read through a bound runner store carries its identity row by then.
_SCOPE_PARENT = "20261009_1000_runner_lease_closures_lease_id_index"


_RUNNER = "rn_01JX0000000000000000000000"


_identity_at_scope_parent = sa.table(
    "runner_identity",
    sa.column("runner_id"),
    sa.column("runner_name"),
    sa.column("registered_at", UtcDateTime()),
)


def _seed_identity_before_the_scope(migration: MigrationRunner, engine: sa.Engine) -> None:
    """Carry the store to the scope revision's parent holding no identity row, then record ``_RUNNER``'s there."""
    migration.upgrade(_SCOPE_PARENT)
    assert _rows(engine, "SELECT count(*) FROM runner_identity") == [(0,)]
    with engine.begin() as conn:
        conn.execute(
            _identity_at_scope_parent.insert().values(runner_id=_RUNNER, runner_name="r-claude", registered_at=_LATER)
        )


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def _rows(engine: sa.Engine, statement: str) -> list[tuple[object, ...]]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(sa.text(statement))]
