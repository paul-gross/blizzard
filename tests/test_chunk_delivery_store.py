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
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.store import schema
from tests.support import chunk_stores, default_tenant, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_NO_REFS = CloseEnqueueDecision(closable=frozenset(), skipped=frozenset())


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
        "ch_1", repo="acme/widget", commit_hash="a1", repository_id="repo_widget", at=_NOW, close_decision=_NO_REFS
    )
    delivery.record_delivery_repo_landed(
        "ch_1", repo="acme/gadget", commit_hash="b2", repository_id=None, at=_NOW, close_decision=_NO_REFS
    )

    landed = schema.delivery_repo_landed
    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                sa.select(landed.c.repo, landed.c.repository_id).where(landed.c.tenant_id == default_tenant(engine))
            )
            .tuples()
            .all()
        )
    assert rows == {"acme/widget": "repo_widget", "acme/gadget": None}


def test_count_landed_since_counts_only_rows_strictly_after(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)
    before = _NOW - timedelta(hours=1)
    after = _NOW + timedelta(hours=1)
    delivery.record_delivery_repo_landed(
        "ch_1", repo="blizzard", commit_hash="a1", repository_id=None, at=before, close_decision=_NO_REFS
    )
    delivery.record_delivery_repo_landed(
        "ch_2", repo="blizzard", commit_hash="b2", repository_id=None, at=after, close_decision=_NO_REFS
    )

    assert delivery.count_landed_since("blizzard", _NOW) == 1


def test_count_landed_since_ignores_a_different_repo(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)
    delivery.record_delivery_repo_landed(
        "ch_1",
        repo="other-repo",
        commit_hash="a1",
        repository_id=None,
        at=_NOW + timedelta(hours=1),
        close_decision=_NO_REFS,
    )

    assert delivery.count_landed_since("blizzard", _NOW) == 0


def test_count_landed_since_is_zero_with_no_landings(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)

    assert delivery.count_landed_since("blizzard", _NOW) == 0


def test_landing_history_pages_in_write_order_not_repo_or_timestamp(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path)
    for chunk, repo, at in [("ch_1", "z", _NOW), ("ch_2", "other", _NOW), ("ch_1", "a", _NOW - timedelta(days=1))]:
        delivery.record_delivery_repo_landed(
            chunk, repo=repo, commit_hash=repo + "-sha", repository_id=None, at=at, close_decision=_NO_REFS
        )
    first = delivery.landing_facts("ch_1", after=0, limit=1)
    second = delivery.landing_facts("ch_1", after=first[0].sequence, limit=1)
    assert [(f.repo, f.commit_hash, f.landed_at) for f in first + second] == [
        ("z", "z-sha", _NOW),
        ("a", "a-sha", _NOW - timedelta(days=1)),
    ]
    assert second[0].sequence > first[0].sequence
    assert delivery.landing_facts("ch_1", after=second[0].sequence, limit=1) == []
