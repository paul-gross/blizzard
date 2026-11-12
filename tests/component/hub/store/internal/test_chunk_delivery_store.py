"""``ChunkDeliveryStore.count_landed_since`` — the routine-baselines read's own
landings-since count (component tier). Migrated-to-head
sqlite-on-disk, the ``tests/component/hub/store/internal/test_finding_store_sets.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.store import schema
from tests.support.hub_node_artifacts import record_landing
from tests.support.scaffolding import (
    chunk_stores,
    chunk_unit,
    default_tenant,
    hub_store_connections,
    migrate_to,
    seed_chunk,
    seed_graph,
)

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_NO_REFS = CloseEnqueueDecision(closable=frozenset(), skipped=frozenset())


def _seeded(tmp_path: Path) -> Engine:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
        seed_chunk(conn, "ch_2", graph_id="gr_1", at=_NOW)
    return engine


def _stores(tmp_path: Path) -> tuple[Engine, ChunkStores]:
    engine = _seeded(tmp_path)
    return engine, chunk_stores(engine, FixedClock(instant=_NOW))


def _land(engine: Engine, stores: ChunkStores, chunk_id: str, **kwargs: Any) -> None:
    with chunk_unit(hub_store_connections(engine), chunk_id) as unit:
        record_landing(stores.artifacts, chunk_id, close_decision=_NO_REFS, transaction=unit, **kwargs)


def test_a_landing_records_the_repository_record_it_resolved_to(tmp_path: Path) -> None:
    engine, stores = _stores(tmp_path)
    _land(engine, stores, "ch_1", repo="acme/widget", commit_hash="a1", repository_id="repo_widget", at=_NOW)
    _land(engine, stores, "ch_1", repo="acme/gadget", commit_hash="b2", repository_id="repo_gadget", at=_NOW)

    landed = schema.delivery_repo_landed
    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                sa.select(landed.c.repo, landed.c.repository_id).where(landed.c.tenant_id == default_tenant(engine))
            )
            .tuples()
            .all()
        )
    assert rows == {"acme/widget": "repo_widget", "acme/gadget": "repo_gadget"}


def test_count_landed_since_counts_only_rows_strictly_after(tmp_path: Path) -> None:
    engine, stores = _stores(tmp_path)
    delivery = stores.delivery
    before = _NOW - timedelta(hours=1)
    after = _NOW + timedelta(hours=1)
    _land(engine, stores, "ch_1", repo="blizzard", commit_hash="a1", at=before)
    _land(engine, stores, "ch_2", repo="blizzard", commit_hash="b2", at=after)

    assert delivery.count_landed_since("blizzard", _NOW) == 1


def test_count_landed_since_ignores_a_different_repo(tmp_path: Path) -> None:
    engine, stores = _stores(tmp_path)
    delivery = stores.delivery
    _land(engine, stores, "ch_1", repo="other-repo", commit_hash="a1", at=_NOW + timedelta(hours=1))

    assert delivery.count_landed_since("blizzard", _NOW) == 0


def test_count_landed_since_is_zero_with_no_landings(tmp_path: Path) -> None:
    delivery = _stores(tmp_path)[1].delivery

    assert delivery.count_landed_since("blizzard", _NOW) == 0


def test_landing_history_pages_in_write_order_not_repo_or_timestamp(tmp_path: Path) -> None:
    engine, stores = _stores(tmp_path)
    delivery = stores.delivery
    for chunk, repo, at in [("ch_1", "z", _NOW), ("ch_2", "other", _NOW), ("ch_1", "a", _NOW - timedelta(days=1))]:
        _land(engine, stores, chunk, repo=repo, commit_hash=repo + "-sha", at=at)
    first = delivery.landing_facts("ch_1", after=0, limit=1)
    second = delivery.landing_facts("ch_1", after=first[0].sequence, limit=1)
    assert [(f.repo, f.commit_hash, f.landed_at) for f in first + second] == [
        ("z", "z-sha", _NOW),
        ("a", "a-sha", _NOW - timedelta(days=1)),
    ]
    assert second[0].sequence > first[0].sequence
    assert delivery.landing_facts("ch_1", after=second[0].sequence, limit=1) == []
