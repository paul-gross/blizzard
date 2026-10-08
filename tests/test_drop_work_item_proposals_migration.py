"""The drop-work-item-proposals revision removes ``work_item_proposals``,
``work_item_materializations``, ``work_item_strikes``, and ``graph_nodes.proposes_work_items``.

Seeded with literal SQL rather than importing ``schema.py``, which no longer declares any of them."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "20261006_1200_runner_minted_ids"
_DROP = "20261008_1000_drop_work_item_proposals"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_TABLES = {"work_item_proposals", "work_item_materializations", "work_item_strikes"}

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
)


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(_GRAPHS).values(
                graph_id="gr_1", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_T0
            )
        )
        conn.execute(sa.insert(_CHUNKS).values(chunk_id="ch_1", graph_id="gr_1", minted_at=_T0))
        conn.execute(
            sa.text(
                "INSERT INTO work_item_proposals"
                " (proposal_id, chunk_id, node_id, node_name, epoch, ordinal, kind, data, proposed_at, runner_id)"
                " VALUES ('wip_1', 'ch_1', 'nd_1', 'build', 1, 0, 'create', '{}', :at, 'r1')"
            ),
            {"at": _T0},
        )
        conn.execute(
            sa.text(
                "INSERT INTO work_item_materializations (proposal_id, outcome, recorded_at)"
                " VALUES ('wip_1', 'unresolved', :at)"
            ),
            {"at": _T0},
        )


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _node_columns(engine: sa.Engine) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns("graph_nodes")}


def test_upgrade_drops_the_proposal_storage_and_downgrade_restores_it_empty(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    try:
        _seed(engine)

        runner.upgrade(_DROP)
        assert not _TABLES & _tables(engine)
        assert "proposes_work_items" not in _node_columns(engine)

        runner.downgrade(_BEFORE)
        assert _tables(engine) >= _TABLES
        assert "proposes_work_items" in _node_columns(engine)
        assert "ix_work_item_proposals_chunk_id" in {
            i["name"] for i in sa.inspect(engine).get_indexes("work_item_proposals")
        }
        assert "uq_work_item_materializations_proposal_id" in {
            c["name"] for c in sa.inspect(engine).get_unique_constraints("work_item_materializations")
        }
        with engine.connect() as conn:
            for table in _TABLES:
                assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0

        runner.upgrade("head")
        assert not _TABLES & _tables(engine)
        assert "proposes_work_items" not in _node_columns(engine)
    finally:
        engine.dispose()
