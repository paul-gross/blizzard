"""The ``20260928_1100_decision_imposed_by_runner`` revision against a store that already holds
decisions — a worker-judged node's decision is backfilled with the runner that held its lease, a
human-judged node's stays NULL, and downgrade drops both new columns (the
``tests/test_garden_proposal_origin_migration.py`` shape)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_BEFORE = "20260928_1000_queue_positions_chunk_id_index"  # the head just before this revision
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _seed(tmp_path: Path):  # type: ignore[no-untyped-def]
    runner, engine = migrate_to(tmp_path, _BEFORE)
    with engine.begin() as conn:
        seed_graph(conn, "g_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="g_1", at=_T0)
        for node_id, name, judged_by in (("nd_w", "build", "worker"), ("nd_h", "approve", "human")):
            conn.execute(
                sa.text(
                    "INSERT INTO graph_nodes (node_id, graph_id, name, executor, session, judged_by)"
                    " VALUES (:node_id, 'g_1', :name, 'runner', 'fresh', :judged_by)"
                ),
                {"node_id": node_id, "name": name, "judged_by": judged_by},
            )
        conn.execute(
            sa.text(
                "INSERT INTO lease_facts (chunk_id, epoch, runner_id, minted_at) VALUES ('ch_1', 1, 'r-gated', :at)"
            ),
            {"at": _T0},
        )
        for decision_id, node_id in (("dec_w", "nd_w"), ("dec_h", "nd_h")):
            conn.execute(
                sa.text(
                    "INSERT INTO decisions (decision_id, chunk_id, node_id, node_name, epoch, choices, submitted_at)"
                    " VALUES (:decision_id, 'ch_1', :node_id, 'n', 1, '[]', :at)"
                ),
                {"decision_id": decision_id, "node_id": node_id, "at": _T0},
            )
    return runner


def _origins(tmp_path: Path) -> dict[str, str | None]:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.connect() as conn:
        return {
            r.decision_id: r.imposed_by_runner_id
            for r in conn.execute(sa.text("SELECT decision_id, imposed_by_runner_id FROM decisions"))
        }


def test_upgrade_backfills_the_imposing_runner_for_a_worker_judged_decision(tmp_path: Path) -> None:
    runner = _seed(tmp_path)

    runner.upgrade("head")

    assert _origins(tmp_path) == {"dec_w": "r-gated", "dec_h": None}


def test_upgrade_leaves_a_worker_judged_decision_with_no_lease_null(tmp_path: Path) -> None:
    runner = _seed(tmp_path)
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM lease_facts"))

    runner.upgrade("head")

    assert _origins(tmp_path) == {"dec_w": None, "dec_h": None}


def test_upgrade_adds_a_nullable_gates_column_reading_as_none(tmp_path: Path) -> None:
    runner = _seed(tmp_path)

    runner.upgrade("head")

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    columns = {c["name"]: c for c in sa.inspect(engine).get_columns("runner_registrations")}
    assert columns["gates"]["nullable"] is True


def test_downgrade_drops_both_columns(tmp_path: Path) -> None:
    runner = _seed(tmp_path)
    runner.upgrade("head")

    runner.downgrade(_BEFORE)

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    inspector = sa.inspect(engine)
    assert "imposed_by_runner_id" not in {c["name"] for c in inspector.get_columns("decisions")}
    assert "gates" not in {c["name"] for c in inspector.get_columns("runner_registrations")}
