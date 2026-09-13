"""``IReadChunkUsageRepository.usage_total_since``'s optional upper bound (issue #183,
blizzard#517, unit tier).

``since`` is inclusive, ``until`` — when given — is exclusive, so two adjacent windows
sharing a boundary instant neither double-count nor drop that fact. Omitting ``until``
returns the open-ended tail. Ported from ``usage_since``'s own window tests (blizzard#517
D3): the seeded token values (1, 2, 4) sum to a distinct total per row set, so the same
four cases still identify exactly which rows a window included."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.store.internal.chunk_usage_store import ChunkUsageStore
from tests.support import hub_store_connections, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _store(tmp_path: Path) -> ChunkUsageStore:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=_T0)
    store = ChunkUsageStore(hub_store_connections(engine), FixedClock(_T0))
    for seconds, tokens in ((0, 1), (5, 2), (10, 4)):
        store.record_usage(
            "ch_a",
            node_id="nd_build",
            epoch=1,
            runner_id="r1",
            kind="spawn",
            model="claude-opus-4-8",
            input_tokens=tokens,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=None,
            at=_at(seconds),
        )
    return store


def test_omitting_until_returns_the_original_open_ended_tail(tmp_path: Path) -> None:
    store = _store(tmp_path)
    total = store.usage_total_since(_at(0))
    assert total.input_tokens == 1 + 2 + 4


def test_until_excludes_a_fact_recorded_exactly_at_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    total = store.usage_total_since(_at(0), until=_at(10))
    assert total.input_tokens == 1 + 2


def test_since_still_includes_a_fact_recorded_exactly_at_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    total = store.usage_total_since(_at(5), until=_at(10))
    assert total.input_tokens == 2


def test_adjacent_windows_sharing_a_boundary_neither_double_count_nor_drop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    earlier = store.usage_total_since(_at(0), until=_at(5))
    later = store.usage_total_since(_at(5), until=_at(15))
    assert earlier.input_tokens == 1
    assert later.input_tokens == 2 + 4
