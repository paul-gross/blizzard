"""``GET /api/queue`` and ``GET /api/backlog`` — the ordered-list read paths (component
tier).

Proves each peek derives the whole fleet's statuses, and each entry's blocked marking (issue
#457), with bulk reads only, so its query count is unchanged as the fleet grows and never
reaches per-chunk ``load_facts`` at all."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.queue import QueueService
from blizzard.hub.domain.work import ChunkFacts
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from tests.support import build_hub, count_queries, hub_store_connections, ingest

pytestmark = pytest.mark.component

_PEEKS = [("/api/queue", True), ("/api/backlog", False)]


def _seed(hub, n: int, *, promote: bool) -> None:  # type: ignore[no-untyped-def]
    for i in range(n):
        ingest(hub, [{"source": "default", "ref": str(i)}], promote=promote)


@pytest.mark.parametrize(("path", "promote"), _PEEKS)
def test_peek_query_count_is_independent_of_fleet_size(tmp_path: Path, path: str, promote: bool) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    _seed(small, 3, promote=promote)
    large = build_hub(tmp_path / "large")
    _seed(large, 9, promote=promote)  # 3x the small fleet

    results: dict[str, int] = {}

    def call(hub, key: str) -> None:  # type: ignore[no-untyped-def]
        resp = hub.client.get(path)
        assert resp.status_code == 200, resp.text
        results[key] = len(resp.json()["entries"])

    small_count = count_queries(small.engine, lambda: call(small, "small"))
    large_count = count_queries(large.engine, lambda: call(large, "large"))

    assert results == {"small": 3, "large": 9}
    assert small_count == large_count


class _CountingFactsStore(ChunkFactsStore):
    """Counts the bulk and per-chunk facts seams, so a test can pin which shape a peek
    actually reaches."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.load_all_facts_calls = 0
        self.load_facts_calls = 0

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        self.load_all_facts_calls += 1
        return super().load_all_facts()

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.load_facts_calls += 1
        return super().load_facts(chunk_id)


@pytest.mark.parametrize(("path", "promote"), _PEEKS)
def test_peek_reads_facts_in_bulk_and_never_per_chunk(tmp_path: Path, path: str, promote: bool) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}], promote=promote)
    ingest(hub, [{"source": "default", "ref": "2"}], promote=promote)

    counting = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    # `list_ready`/`list_not_ready` route through the record seam's own facts collaborator,
    # so the record store handed to the queue service must be wired to the same counting
    # instance for its bulk/per-chunk facts calls to register.
    record = ChunkRecordStore(hub_store_connections(hub.engine), hub.clock, facts=counting)
    assert hub.app is not None
    # The peek reads through the queue service's own store handle, not `services.chunks`.
    hub.app.state.services = replace(
        hub.services,
        chunks=replace(hub.services.chunks, facts=counting, record=record),
        queue=QueueService(
            queue=cast(IWriteChunkQueueRepository, hub.services.chunks.queue), record=record, clock=hub.clock
        ),
    )

    resp = hub.client.get(path)

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["entries"]) == 2
    # One bulk read resolves the candidate list, a second derives each entry's blocked
    # marking (issue #457) — both bulk, neither per-chunk.
    assert counting.load_all_facts_calls == 2
    assert counting.load_facts_calls == 0
