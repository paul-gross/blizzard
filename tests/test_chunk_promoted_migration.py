"""The chunk-promoted back-fill — existing chunks stay claimable after the not-ready state lands (D-103).

Adding the not-ready resting state makes an un-promoted chunk derive ``not_ready``. A bare
table create would silently un-ready every chunk already in flight, so the chunk-promoted migration
back-fills a ``chunk.promoted`` fact for every pre-existing chunk. This exercises that on a
store migrated to the revision *before* ``chunk_promoted``, carrying a chunk minted the old
way: after the upgrade it must derive ``ready`` (claimable), unaffected by the change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.work import ChunkStatus, derive_chunk_status
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store import ChunkStore

pytestmark = pytest.mark.component

_BEFORE = "20260714_0819_hub_delivery_pr_facts"  # the head just before chunk_promoted
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_backfill_keeps_preexisting_chunks_ready(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))

    # Migrate to the revision before chunk_promoted, then mint a chunk the old way.
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(s.graphs).values(graph_id="gr_1", name="g", entry_node_id="nd_1", definition_yaml="", created_at=_T0)
        )
        conn.execute(insert(s.chunks).values(chunk_id="ch_legacy", graph_id="gr_1", minted_at=_T0))

    # Upgrade to head — the chunk-promoted migration adds the table and back-fills the pre-existing chunk.
    runner.upgrade("head")

    store = ChunkStore(engine, FixedClock(_T0))
    facts = store.load_facts("ch_legacy")
    assert facts is not None and facts.promoted is True
    assert derive_chunk_status(facts) is ChunkStatus.READY  # unaffected — still claimable
    assert [c.chunk_id for c in store.list_ready()] == ["ch_legacy"]
