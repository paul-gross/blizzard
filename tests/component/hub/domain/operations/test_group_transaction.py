"""Grouping shares one unit across its reads, pointer writes and fold."""

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk, WorkRef
from blizzard.hub.domain.operations.queue import GroupService
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.transactions import HubTransactions
from tests.support.scaffolding import hub_store_connections, migrate_to, seed_chunk_record, seed_graph


def test_group_returns_joined_survivor_and_rolls_back_on_fold_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=now)
    store = hub_store_connections(engine)
    transactions = HubTransactions(store)
    clock = FixedClock(now)
    chunks = build_chunk_stores(store, clock)
    ref = WorkRef(source="default", ref="1")
    for chunk_id, refs in (("ch_a", []), ("ch_b", [ref])):
        seed_chunk_record(chunks, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=refs, minted_at=now))
    service = GroupService(
        work_refs=chunks.work_refs,
        dependencies=chunks.dependencies,
        transactions=transactions,
        record=chunks.record,
        facts=chunks.facts,
        clock=clock,
        cycle_lock=threading.Lock(),
    )
    original = chunks.dependencies.record_fold

    def fail(*args: object, **kwargs: object) -> object:
        raise ValueError("abort")

    with monkeypatch.context() as patch:
        patch.setattr(chunks.dependencies, "record_fold", fail)
        with pytest.raises(ValueError):
            service.group("ch_a", ["ch_b"])
    survivor = chunks.record.get("ch_a")
    assert survivor is not None
    assert survivor.work_refs == []
    assert chunks.record.get("ch_b") is not None
    assert chunks.dependencies.record_fold == original
    result = service.group("ch_a", ["ch_b"])
    assert result.survivor.work_refs == [ref]
    assert result.grouped_id is not None
    assert chunks.record.get("ch_b") is None
    engine.dispose()
