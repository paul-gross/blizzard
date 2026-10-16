"""Tests proving the ``20260930_1000_epoch_owners`` revision."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.tools.invariants import HubInvariants
from tests.migration_support_store_migrations import (
    assert_frozen_table_lacks_a_later_revisions_columns,
)
from tests.support import (
    migrate_to,
    seed_chunk,
    seed_graph,
)

_PRE_EPOCH_OWNERS = "20260930_1000_artifact_seq"


@pytest.mark.component
def test_a_backfilled_epoch_with_two_runners_lease_rows_is_clean(tmp_path: Path) -> None:
    """The real backfill owns an epoch by its first lease row, so a later level row from
    another runner — legacy data — is not a violation."""
    runner, engine = migrate_to(tmp_path, "20260930_1000_artifact_seq")
    at = "2026-09-29 12:00:00"
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=datetime(2026, 9, 29, tzinfo=UTC))
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=datetime(2026, 9, 29, tzinfo=UTC))
        for runner_id in ("r_a", "r_b"):
            conn.execute(
                sa.text(
                    "insert into lease_facts (chunk_id, epoch, runner_id, minted_at) "
                    "values ('ch_1', 1, :runner_id, :at)"
                ),
                {"runner_id": runner_id, "at": at},
            )
    runner.upgrade("head")
    try:
        assert [v for v in HubInvariants(engine).run() if v.invariant == "hub:epochs-owned"] == []
    finally:
        engine.dispose()


@pytest.mark.unit
def test_epoch_owners_backfill_restarts_to_the_hub_and_runner_epochs_to_their_earliest_runner(
    tmp_path: Path,
) -> None:
    """Every restart epoch becomes hub-owned; every epoch still unowned takes the runner of
    its earliest lease row, a ``hub`` lease meaning hub-owned. Downgrading past the revision
    drops the table and the ``lease_facts.lease_id`` column again."""
    runner, engine = migrate_to(tmp_path, _PRE_EPOCH_OWNERS)
    at = "2026-09-29 12:00:00"
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=datetime(2026, 9, 29, tzinfo=UTC))
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=datetime(2026, 9, 29, tzinfo=UTC))
        leases = [
            (1, "r_a"),  # earliest at epoch 1 — owns it
            (1, "r_b"),  # a later, level mint at epoch 1 — the legacy level bug
            (2, "hub"),  # a hub mint
            (3, "r_a"),  # a runner lease at a restart epoch — the restart owns it
            (4, "r_b"),
        ]
        for epoch, runner_id in leases:
            conn.execute(
                sa.text(
                    "insert into lease_facts (chunk_id, epoch, runner_id, minted_at) "
                    "values ('ch_1', :epoch, :runner_id, :at)"
                ),
                {"epoch": epoch, "runner_id": runner_id, "at": at},
            )
        conn.execute(
            sa.text(
                "insert into chunk_restarts (chunk_id, graph_id, to_node_id, epoch, restarted_by, recorded_at) "
                "values ('ch_1', 'gr_1', 'nd_1', 3, 'op', :at)"
            ),
            {"at": at},
        )

    runner.upgrade("head")

    with engine.connect() as conn:
        rows = conn.execute(sa.text("select epoch, runner_id from epoch_owners where chunk_id = 'ch_1'")).all()
    owners = {row.epoch: row.runner_id for row in rows}
    assert owners == {1: "r_a", 2: None, 3: None, 4: "r_b"}
    assert "lease_id" in {c["name"] for c in sa.inspect(engine).get_columns("lease_facts")}

    runner.downgrade(_PRE_EPOCH_OWNERS)
    assert "epoch_owners" not in sa.inspect(engine).get_table_names()
    assert "lease_id" not in {c["name"] for c in sa.inspect(engine).get_columns("lease_facts")}

    runner.upgrade("head")
    assert "epoch_owners" in sa.inspect(engine).get_table_names()
    engine.dispose()


@pytest.mark.unit
@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub", "20260930_1000_artifact_seq", "lease_facts", ("lease_id",), "added", id="hub.lease_facts.lease_id"
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
