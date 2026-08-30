"""The ``chunk_deleted`` table create — pure additive, no backfill (issue #364).

Unlike ``chunk_promoted``'s own migration, a chunk minted before this one carries no
``chunk_deleted`` row either way, so it stays fully live and claimable after the
upgrade with no back-fill step — this pins that, and that the fresh table is honored
immediately."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store import ChunkStore

pytestmark = pytest.mark.component

_BEFORE = "20260819_2200_chunk_restart_from_graph"  # the head just before chunk_deleted
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_upgrade_creates_chunk_deleted_and_leaves_preexisting_chunks_claimable(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))

    # Migrate to the revision before chunk_deleted, then mint and promote a chunk the old way.
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(s.graphs).values(graph_id="gr_1", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_T0)
        )
        conn.execute(insert(s.chunks).values(chunk_id="ch_legacy", graph_id="gr_1", minted_at=_T0))
        conn.execute(insert(s.chunk_promoted).values(chunk_id="ch_legacy", promoted_at=_T0))

    # Upgrade to head — the chunk_deleted migration adds the table; the pre-existing
    # chunk carries no row in it, so it is unaffected.
    runner.upgrade("head")

    store = ChunkStore(engine, FixedClock(_T0))
    assert store.get("ch_legacy") is not None
    facts = store.load_facts("ch_legacy")
    assert facts is not None and facts.status() is ChunkStatus.READY  # unaffected — still claimable
    assert [c.chunk_id for c in store.list_ready()] == ["ch_legacy"]

    # The fresh table is honored immediately: a row in it makes the chunk ephemeral.
    with engine.begin() as conn:
        conn.execute(insert(s.chunk_deleted).values(chunk_id="ch_legacy", deleted_at=_T0, deleted_by="operator"))
    assert store.get("ch_legacy") is None
