"""The artifact-seq revision's per-chunk ``seq`` backfill and its round trip.

Seeds pre-``seq`` rows on a store migrated to the revision just before it, via a local
frozen literal rather than the live schema module (a revision pinned in time must not
read a moving shape)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_BEFORE = "20260929_1100_drop_open_pr_facts"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_OLD_ARTIFACTS = sa.Table(
    "artifacts",
    sa.MetaData(),
    sa.Column("artifact_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("node_name", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("data", sa.Text, nullable=False),
    sa.Column("produced_at", sa.DateTime, nullable=False),
)


def _seed(conn: sa.Connection, artifact_id: str, chunk_id: str, *, epoch: int, at: datetime) -> None:
    conn.execute(
        sa.insert(_OLD_ARTIFACTS).values(
            artifact_id=artifact_id,
            chunk_id=chunk_id,
            node_id="nd_1",
            node_name="deliver",
            epoch=epoch,
            name=artifact_id,
            kind="asset",
            data="",
            produced_at=at,
        )
    )


def _seqs(engine: sa.Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return dict(conn.execute(sa.text("SELECT artifact_id, seq FROM artifacts")).tuples().all())


def test_backfill_orders_each_chunk_by_epoch_then_produced_at_then_id(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _BEFORE)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=_T0)
        seed_chunk(conn, "ch_b", graph_id="gr_1", at=_T0)
        _seed(conn, "art_a_epoch2", "ch_a", epoch=2, at=_T0)
        _seed(conn, "art_a_late", "ch_a", epoch=1, at=_T0 + timedelta(seconds=5))
        _seed(conn, "art_a_tie_z", "ch_a", epoch=1, at=_T0)
        _seed(conn, "art_a_tie_b", "ch_a", epoch=1, at=_T0)
        _seed(conn, "art_b_only", "ch_b", epoch=1, at=_T0)

    runner.upgrade("head")

    assert _seqs(engine) == {
        "art_a_tie_b": 1,
        "art_a_tie_z": 2,
        "art_a_late": 3,
        "art_a_epoch2": 4,
        "art_b_only": 1,  # each chunk's counter starts fresh
    }


def test_upgrade_downgrade_round_trip_keeps_rows_and_toggles_seq(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _BEFORE)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
        _seed(conn, "art_1", "ch_1", epoch=1, at=_T0)

    def columns() -> dict[str, bool]:
        with engine.connect() as conn:
            return {c["name"]: c["nullable"] for c in sa.inspect(conn).get_columns("artifacts")}

    runner.upgrade("head")
    assert columns()["seq"] is False
    runner.downgrade(_BEFORE)
    assert "seq" not in columns()
    with engine.connect() as conn:
        assert conn.execute(sa.select(_OLD_ARTIFACTS.c.artifact_id)).scalars().all() == ["art_1"]
    runner.upgrade("head")
    assert _seqs(engine) == {"art_1": 1}
