"""``IReadChunkLifecycleRepository.is_ephemeral`` (issue #456, component tier) — the read
that tells a grouped-away or deleted chunk id apart from one never minted at all, since
``IReadChunkRecordRepository.get`` answers ``None`` for both."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.store.internal.chunk_rows import record_deleted_row, record_grouped_row_conn
from tests.support import chunk_stores, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 2, tzinfo=UTC)


def test_is_ephemeral_is_false_for_a_live_chunk(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_live", graph_id="gr_1", at=_NOW)
    lifecycle = chunk_stores(engine, FixedClock(instant=_NOW)).lifecycle

    assert lifecycle.is_ephemeral("ch_live") is False


def test_is_ephemeral_is_true_for_a_grouped_away_chunk(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_survivor", graph_id="gr_1", at=_NOW)
        seed_chunk(conn, "ch_grouped", graph_id="gr_1", at=_NOW)
        record_grouped_row_conn(conn, "ch_grouped", grouped_into="ch_survivor", at=_NOW)
    lifecycle = chunk_stores(engine, FixedClock(instant=_NOW)).lifecycle

    assert lifecycle.is_ephemeral("ch_grouped") is True


def test_is_ephemeral_is_true_for_a_deleted_chunk(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_deleted", graph_id="gr_1", at=_NOW)
        record_deleted_row(conn, "ch_deleted", by="operator", at=_NOW)
    lifecycle = chunk_stores(engine, FixedClock(instant=_NOW)).lifecycle

    assert lifecycle.is_ephemeral("ch_deleted") is True


def test_is_ephemeral_is_false_for_an_id_never_minted(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    lifecycle = chunk_stores(engine, FixedClock(instant=_NOW)).lifecycle

    assert lifecycle.is_ephemeral("ch_never_minted") is False
