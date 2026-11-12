"""The ``chunk_demoted`` fact against every reader of "currently promoted" (component tier).

A chunk is currently promoted while its newest promotion is later than its newest demotion. Proves the facts load
and ``promoted_ats`` each read it that way."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, insert

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.store import schema as s
from tests.support.scaffolding import (
    chunk_stores,
    migrate_to,
    seed_chunk_record,
    seed_graph,
    seed_promote,
    stamp,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    stores = chunk_stores(engine, FixedClock(_T0))
    seed_chunk_record(stores, Chunk(chunk_id="ch_a", graph_id="gr_1", work_refs=[], minted_at=_T0))
    return stores, engine


def _demote(engine: Engine, chunk_id: str, *, at: datetime) -> None:
    with engine.begin() as conn:
        conn.execute(insert(s.chunk_demoted).values(stamp(s.chunk_demoted, chunk_id=chunk_id, demoted_at=at)))


def _pause(engine: Engine, chunk_id: str, *, at: datetime) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(s.chunk_pause_facts).values(
                stamp(s.chunk_pause_facts, chunk_id=chunk_id, paused=True, set_at=at, set_by="op")
            )
        )


def _status(stores: ChunkStores, chunk_id: str) -> ChunkStatus:
    facts = stores.facts.load_facts(chunk_id)
    assert facts is not None
    return facts.status()


def test_a_demoted_chunk_derives_not_ready(tmp_path: Path) -> None:
    stores, engine = _store(tmp_path)
    seed_promote(stores, engine, "ch_a", at=_at(1))
    assert _status(stores, "ch_a") is ChunkStatus.READY

    _demote(engine, "ch_a", at=_at(2))

    assert _status(stores, "ch_a") is ChunkStatus.NOT_READY
    assert stores.facts.status_facts_for(["ch_a"])["ch_a"].promoted is False


def test_a_demoted_chunk_with_its_brake_set_derives_paused_and_not_ready_but_for_pause(tmp_path: Path) -> None:
    stores, engine = _store(tmp_path)
    seed_promote(stores, engine, "ch_a", at=_at(1))
    _demote(engine, "ch_a", at=_at(2))
    _pause(engine, "ch_a", at=_at(3))

    facts = stores.facts.load_facts("ch_a")
    assert facts is not None
    assert facts.status() is ChunkStatus.PAUSED
    assert facts.is_ready_but_for_pause() is False


def test_promoted_ats_reads_the_newest_promotion_and_omits_a_demoted_chunk(tmp_path: Path) -> None:
    stores, engine = _store(tmp_path)
    seed_promote(stores, engine, "ch_a", at=_at(1))
    _demote(engine, "ch_a", at=_at(2))
    assert stores.queue.promoted_ats(["ch_a"]) == {}

    seed_promote(stores, engine, "ch_a", at=_at(3))

    assert stores.queue.promoted_ats(["ch_a"]) == {"ch_a": _at(3)}
