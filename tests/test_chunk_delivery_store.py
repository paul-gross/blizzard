"""``ChunkDeliveryStore.count_landed_since`` — the routine-baselines read's own
landings-since count (component tier). Migrated-to-head
sqlite-on-disk, the ``tests/test_finding_set_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.store import schema
from tests.support import chunk_stores, default_tenant, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seeded(tmp_path: Path) -> Engine:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
        seed_chunk(conn, "ch_2", graph_id="gr_1", at=_NOW)
    return engine


def _delivery(tmp_path: Path) -> IWriteChunkDeliveryRepository:
    return chunk_stores(_seeded(tmp_path), FixedClock(instant=_NOW)).delivery


def test_a_landing_records_the_repository_record_it_resolved_to_or_none(tmp_path: Path) -> None:
    engine = _seeded(tmp_path)
    delivery = chunk_stores(engine, FixedClock(instant=_NOW)).delivery
    delivery.record_delivery_repo_landed(
        "ch_1", repo="acme/widget", commit_hash="a1", repository_name="widget", at=_NOW
    )
    delivery.record_delivery_repo_landed("ch_1", repo="acme/gadget", commit_hash="b2", repository_name=None, at=_NOW)

    landed = schema.delivery_repo_landed
    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                sa.select(landed.c.repo, landed.c.repository_name).where(landed.c.tenant_id == default_tenant(engine))
            )
            .tuples()
            .all()
        )
    assert rows == {"acme/widget": "widget", "acme/gadget": None}


def test_count_landed_since_counts_only_rows_strictly_after(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)
    before = _NOW - timedelta(hours=1)
    after = _NOW + timedelta(hours=1)
    delivery.record_delivery_repo_landed("ch_1", repo="blizzard", commit_hash="a1", repository_name=None, at=before)
    delivery.record_delivery_repo_landed("ch_2", repo="blizzard", commit_hash="b2", repository_name=None, at=after)

    assert delivery.count_landed_since("blizzard", _NOW) == 1


def test_count_landed_since_ignores_a_different_repo(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)
    delivery.record_delivery_repo_landed(
        "ch_1", repo="other-repo", commit_hash="a1", repository_name=None, at=_NOW + timedelta(hours=1)
    )

    assert delivery.count_landed_since("blizzard", _NOW) == 0


def test_count_landed_since_is_zero_with_no_landings(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)

    assert delivery.count_landed_since("blizzard", _NOW) == 0
