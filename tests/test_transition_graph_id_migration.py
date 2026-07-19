"""The transition-graph-id revision — add ``transitions.graph_id`` + backfill (issue #90).

Exercises the backfill on a store migrated to the revision just before it
(``route_token_minted``), seeded with transitions in the **pre-graph_id** shape across
two chunks pinned to two different graphs. Asserts each transition backfills to *its
chunk's* graph pin, that a fresh store reaches head with the column NOT NULL, that the
revision is idempotent, and that down-then-up re-derives the identical provenance.

Seeded with revision-pinned ``sa.Table`` literals rather than ``from
blizzard.hub.store import schema as s`` — the same reason the migration itself freezes
its literals (see its docstring): ``schema.py`` is head-of-tree and would move under a
test pinned to a revision before the reshape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "20260718_1200_hub_route_token_minted"  # the head just before graph-provenance
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_GRAPHS = sa.Table(
    "graphs",
    sa.MetaData(),
    sa.Column("graph_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("entry_node_id", sa.String, nullable=False),
    sa.Column("definition_yaml", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

_CHUNKS = sa.Table(
    "chunks",
    sa.MetaData(),
    sa.Column("chunk_id", sa.String, primary_key=True),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("minted_at", sa.DateTime, nullable=False),
    sa.Column("model", sa.String, nullable=False),
)

# The pre-graph_id transition shape — no ``graph_id`` column yet.
_OLD_TRANSITIONS = sa.Table(
    "transitions",
    sa.MetaData(),
    sa.Column("transition_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("from_node_id", sa.String, nullable=True),
    sa.Column("to_node_id", sa.String, nullable=False),
    sa.Column("choice_name", sa.String, nullable=True),
    sa.Column("decision_id", sa.String, nullable=True),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("recorded_at", sa.DateTime, nullable=False),
)

_NEW_TRANSITIONS = sa.Table(
    "transitions",
    sa.MetaData(),
    sa.Column("transition_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
)


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        for gid in ("gr_a", "gr_b"):
            conn.execute(
                sa.insert(_GRAPHS).values(
                    graph_id=gid, name=gid, entry_node_id="nd_1", definition_yaml="", created_at=_T0
                )
            )
        conn.execute(sa.insert(_CHUNKS).values(chunk_id="ch_a", graph_id="gr_a", minted_at=_T0, model="m"))
        conn.execute(sa.insert(_CHUNKS).values(chunk_id="ch_b", graph_id="gr_b", minted_at=_T0, model="m"))
        # Two transitions on ch_a (pinned to gr_a), one on ch_b (pinned to gr_b).
        for tid, cid in (("tr_1", "ch_a"), ("tr_2", "ch_a"), ("tr_3", "ch_b")):
            conn.execute(
                sa.insert(_OLD_TRANSITIONS).values(
                    transition_id=tid,
                    chunk_id=cid,
                    from_node_id="nd_1",
                    to_node_id="nd_2",
                    choice_name="pass",
                    epoch=1,
                    runner_id="r",
                    recorded_at=_T0,
                )
            )


def _runner(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    return migration_runner(HubConfig(root=tmp_path, db_url=db_url)), db_url


def test_each_transition_backfills_to_its_chunks_graph_pin(tmp_path: Path) -> None:
    runner, db_url = _runner(tmp_path)
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = {r.transition_id: r.graph_id for r in conn.execute(sa.select(_NEW_TRANSITIONS))}
    assert rows == {"tr_1": "gr_a", "tr_2": "gr_a", "tr_3": "gr_b"}


def test_upgrade_is_idempotent_over_an_already_reshaped_store(tmp_path: Path) -> None:
    runner, db_url = _runner(tmp_path)
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")

    with engine.connect() as conn:
        first = {r.transition_id: r.graph_id for r in conn.execute(sa.select(_NEW_TRANSITIONS))}
    runner.upgrade("head")  # second pass over the reshaped store
    with engine.connect() as conn:
        second = {r.transition_id: r.graph_id for r in conn.execute(sa.select(_NEW_TRANSITIONS))}
    assert second == first == {"tr_1": "gr_a", "tr_2": "gr_a", "tr_3": "gr_b"}


def test_down_then_up_re_derives_the_identical_provenance(tmp_path: Path) -> None:
    runner, db_url = _runner(tmp_path)
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)
    runner.upgrade("head")
    with engine.connect() as conn:
        before = {r.transition_id: r.graph_id for r in conn.execute(sa.select(_NEW_TRANSITIONS))}

    runner.downgrade(_BEFORE)
    runner.upgrade("head")

    with engine.connect() as conn:
        after = {r.transition_id: r.graph_id for r in conn.execute(sa.select(_NEW_TRANSITIONS))}
    assert after == before


def test_a_fresh_store_reaches_head_with_graph_id_not_null(tmp_path: Path) -> None:
    runner, db_url = _runner(tmp_path)
    engine = create_engine_from_url(db_url)

    runner.upgrade("head")

    with engine.connect() as conn:
        cols = {c["name"]: c for c in sa.inspect(conn).get_columns("transitions")}
    assert "graph_id" in cols
    assert cols["graph_id"]["nullable"] is False


def test_the_walking_skeleton_revision_creates_the_pre_graph_id_shape(tmp_path: Path) -> None:
    """The walking-skeleton revision must freeze ``transitions`` at its pre-#90 shape.

    Its create once imported ``transitions`` from head-of-tree ``schema.py``; once
    ``schema.py`` gained ``graph_id`` that import would materialize the post-reshape
    column at the walking-skeleton revision — and this revision's ``if "graph_id" in
    columns: return`` guard would then fire, making its backfill dead on every fresh
    store while the live store still needed it. The walking-skeleton revision now carries
    its own frozen literal; this asserts the freeze holds from both ends."""
    runner, db_url = _runner(tmp_path)
    engine = create_engine_from_url(db_url)

    def columns() -> set[str]:
        with engine.connect() as conn:
            return {c["name"] for c in sa.inspect(conn).get_columns("transitions")}

    runner.upgrade("20260713_1218_hub_walking_skeleton")
    assert "graph_id" not in columns(), "the walking-skeleton revision leaked head-of-tree schema.py's graph_id"

    runner.upgrade("head")
    assert "graph_id" in columns(), "the graph-provenance revision must reshape a fresh store, not no-op"
