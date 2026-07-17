"""The 0014 route-event ``seq`` backfill (issue #41).

Exercises the backfill on a store migrated to ``0013`` (the revision *before* 0014),
seeded with pre-existing ``route_created``/``route_released`` rows in the pre-``seq``
shape. Seeded with a local frozen literal rather than ``from blizzard.hub.store import
schema as s`` (the same reason ``test_pm_pointer_migration.py`` does: a revision pinned
in time must not read a moving shape — see 0002's and 0014's module docstrings).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_BEFORE = "0013_hub_pm_pointer_source_ref"  # the head just before the route seq tiebreak
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_OLD_ROUTE_CREATED = sa.Table(
    "route_created",
    sa.MetaData(),
    sa.Column("route_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("workspace_id", sa.String, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

_OLD_ROUTE_RELEASED = sa.Table(
    "route_released",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("released_at", sa.DateTime, nullable=False),
)

_NEW_ROUTE_CREATED = sa.Table(
    "route_created",
    sa.MetaData(),
    sa.Column("route_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
)

_NEW_ROUTE_RELEASED = sa.Table(
    "route_released",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("released_at", sa.DateTime, nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
)


def _seed_chunk(conn: sa.Connection, chunk_id: str) -> None:
    seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)


def _migrate_to_before(tmp_path: Path):
    runner, engine = migrate_to(tmp_path, _BEFORE)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return runner, engine


def test_chronologically_ordered_rows_backfill_seq_in_order(tmp_path: Path) -> None:
    runner, engine = _migrate_to_before(tmp_path)
    with engine.begin() as conn:
        _seed_chunk(conn, "ch_1")
        conn.execute(
            sa.insert(_OLD_ROUTE_CREATED).values(
                route_id="rt_1", chunk_id="ch_1", runner_id="r1", workspace_id="w1", created_at=_T0
            )
        )
        conn.execute(sa.insert(_OLD_ROUTE_RELEASED).values(chunk_id="ch_1", released_at=_T0 + timedelta(seconds=5)))

    runner.upgrade("head")

    with engine.connect() as conn:
        created = conn.execute(sa.select(_NEW_ROUTE_CREATED).where(_NEW_ROUTE_CREATED.c.chunk_id == "ch_1")).one()
        released = conn.execute(sa.select(_NEW_ROUTE_RELEASED).where(_NEW_ROUTE_RELEASED.c.chunk_id == "ch_1")).one()
    assert created.seq < released.seq  # the release genuinely postdates the create


def test_same_instant_rows_backfill_created_before_released(tmp_path: Path) -> None:
    """The documented backfill default for a historical tie (see 0014's docstring):
    created sorts first, so a pre-existing same-instant pair backfills as though the
    release outranked the create — the same "release wins" bias ``route_of`` used to
    hard-code, kept only here since a real historical tie is not expected to exist."""
    runner, engine = _migrate_to_before(tmp_path)
    with engine.begin() as conn:
        _seed_chunk(conn, "ch_tied")
        conn.execute(
            sa.insert(_OLD_ROUTE_CREATED).values(
                route_id="rt_tied", chunk_id="ch_tied", runner_id="r1", workspace_id="w1", created_at=_T0
            )
        )
        conn.execute(sa.insert(_OLD_ROUTE_RELEASED).values(chunk_id="ch_tied", released_at=_T0))

    runner.upgrade("head")

    with engine.connect() as conn:
        created = conn.execute(sa.select(_NEW_ROUTE_CREATED).where(_NEW_ROUTE_CREATED.c.chunk_id == "ch_tied")).one()
        released = conn.execute(sa.select(_NEW_ROUTE_RELEASED).where(_NEW_ROUTE_RELEASED.c.chunk_id == "ch_tied")).one()
    assert created.seq == 1
    assert released.seq == 2


def test_seq_is_scoped_per_chunk(tmp_path: Path) -> None:
    runner, engine = _migrate_to_before(tmp_path)
    with engine.begin() as conn:
        _seed_chunk(conn, "ch_a")
        _seed_chunk(conn, "ch_b")
        conn.execute(
            sa.insert(_OLD_ROUTE_CREATED).values(
                route_id="rt_a", chunk_id="ch_a", runner_id="r1", workspace_id="w1", created_at=_T0
            )
        )
        conn.execute(
            sa.insert(_OLD_ROUTE_CREATED).values(
                route_id="rt_b", chunk_id="ch_b", runner_id="r1", workspace_id="w1", created_at=_T0
            )
        )

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = {r.chunk_id: r.seq for r in conn.execute(sa.select(_NEW_ROUTE_CREATED))}
    assert rows["ch_a"] == 1
    assert rows["ch_b"] == 1  # each chunk's counter starts fresh, not a global sequence


def test_downgrade_drops_the_seq_column(tmp_path: Path) -> None:
    runner, engine = _migrate_to_before(tmp_path)
    with engine.begin() as conn:
        _seed_chunk(conn, "ch_1")
        conn.execute(
            sa.insert(_OLD_ROUTE_CREATED).values(
                route_id="rt_1", chunk_id="ch_1", runner_id="r1", workspace_id="w1", created_at=_T0
            )
        )
    runner.upgrade("head")

    runner.downgrade(_BEFORE)

    with engine.connect() as conn:
        columns = {c["name"] for c in sa.inspect(conn).get_columns("route_created")}
    assert "seq" not in columns
    with engine.connect() as conn:
        row = conn.execute(sa.select(_OLD_ROUTE_CREATED).where(_OLD_ROUTE_CREATED.c.chunk_id == "ch_1")).one()
    assert row.route_id == "rt_1"  # the pre-existing row survives the round trip


def test_a_fresh_store_reaches_0013_in_the_pre_seq_shape(tmp_path: Path) -> None:
    """0002 must materialize route_created/route_released without ``seq``, not
    head-of-tree ``schema.py``'s shape — the same freeze 0013 established for
    ``chunk_pm_pointers`` (see 0002's and 0014's module docstrings)."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    engine = create_engine_from_url(db_url)

    def columns(table: str) -> set[str]:
        with engine.connect() as conn:
            return {c["name"] for c in sa.inspect(conn).get_columns(table)}

    runner.upgrade("0002_hub_walking_skeleton")
    assert "seq" not in columns("route_created")
    assert "seq" not in columns("route_released")

    runner.upgrade("head")
    assert "seq" in columns("route_created")
    assert "seq" in columns("route_released")
