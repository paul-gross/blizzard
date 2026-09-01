"""``ChunkDeliveryStore.count_landed_since`` — the routine-baselines read's own
landings-since count (blizzard#392 D1, D5, component tier). Migrated-to-head
sqlite-on-disk, the ``test_finding_set_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.delivery import IWriteChunkDeliveryRepository
from tests.support import chunk_stores, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _delivery(tmp_path: Path) -> IWriteChunkDeliveryRepository:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
        seed_chunk(conn, "ch_2", graph_id="gr_1", at=_NOW)
    return chunk_stores(engine, FixedClock(instant=_NOW)).delivery


def test_count_landed_since_counts_only_rows_strictly_after(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)
    before = _NOW - timedelta(hours=1)
    after = _NOW + timedelta(hours=1)
    delivery.record_delivery_repo_landed("ch_1", repo="blizzard", commit_hash="a1", at=before)
    delivery.record_delivery_repo_landed("ch_2", repo="blizzard", commit_hash="b2", at=after)

    assert delivery.count_landed_since("blizzard", _NOW) == 1


def test_count_landed_since_ignores_a_different_repo(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)
    delivery.record_delivery_repo_landed("ch_1", repo="other-repo", commit_hash="a1", at=_NOW + timedelta(hours=1))

    assert delivery.count_landed_since("blizzard", _NOW) == 0


def test_count_landed_since_is_zero_with_no_landings(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)

    assert delivery.count_landed_since("blizzard", _NOW) == 0
