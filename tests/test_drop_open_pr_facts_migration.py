"""The drop-open-pr-facts revision removes ``delivery_pr_opened`` and ``delivery_pr_closed``.

Seeded with literal ``sa.Table`` shapes rather than importing ``src/blizzard/hub/store/schema.py``, which no longer
declares either table."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

pytestmark = pytest.mark.component

_BEFORE = "20260929_1000_decision_imposed_by_runner"
_DROP = "20260929_1100_drop_open_pr_facts"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_TABLES = {"delivery_pr_opened", "delivery_pr_closed"}

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
                "INSERT INTO delivery_pr_opened (chunk_id, repo, pr_number, pr_url, commit_hash, opened_at)"
                " VALUES ('ch_1', 'acme/widget', 1, 'http://forge/acme/widget/pull/1', 'abc', :at)"
            ),
            {"at": _T0},
        )
        conn.execute(
            sa.text(
                "INSERT INTO delivery_pr_closed (chunk_id, repo, pr_number, merged, closed_at)"
                " VALUES ('ch_1', 'acme/widget', 1, 1, :at)"
            ),
            {"at": _T0},
        )


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def test_upgrade_drops_both_tables_and_downgrade_restores_them_empty(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    _seed(engine)

    runner.upgrade(_DROP)
    assert not _TABLES & _tables(engine)

    runner.downgrade(_BEFORE)
    assert _tables(engine) >= _TABLES
    assert "uq_delivery_pr_opened_chunk_repo" in {
        c["name"] for c in sa.inspect(engine).get_unique_constraints("delivery_pr_opened")
    }
    assert "ix_delivery_pr_closed_chunk_id" in {i["name"] for i in sa.inspect(engine).get_indexes("delivery_pr_closed")}
    with engine.connect() as conn:
        for table in _TABLES:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0

    runner.upgrade("head")
    assert not _TABLES & _tables(engine)
