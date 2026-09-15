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

from blizzard.foundation.chunk_status import ChunkStatus
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
        self.load_all_statuses_calls = 0
        self.load_facts_calls = 0

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        self.load_all_facts_calls += 1
        return super().load_all_facts()

    def load_all_statuses(self) -> dict[str, ChunkStatus]:
        self.load_all_statuses_calls += 1
        return super().load_all_statuses()

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.load_facts_calls += 1
        return super().load_facts(chunk_id)


def _wire_counting_facts(hub) -> _CountingFactsStore:  # type: ignore[no-untyped-def]
    """Swap the hub's facts seam for a counting one, wiring the queue service's own
    record store over the same counting instance — `list_ready`/`list_not_ready` no
    longer touch facts at all (they take an already-derived ``statuses`` map), but the
    route itself derives that map from the facts seam once per request, and
    `_blocked_markings` no longer touches facts either."""
    counting = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    record = ChunkRecordStore(hub_store_connections(hub.engine), hub.clock)
    assert hub.app is not None
    # The peek reads through the queue service's own store handle, not `services.chunks`.
    hub.app.state.services = replace(
        hub.services,
        chunks=replace(hub.services.chunks, facts=counting, record=record),
        queue=QueueService(
            queue=cast(IWriteChunkQueueRepository, hub.services.chunks.queue), record=record, clock=hub.clock
        ),
    )
    return counting


@pytest.mark.parametrize(("path", "promote"), _PEEKS)
def test_peek_reads_statuses_in_bulk_once_and_never_per_chunk(tmp_path: Path, path: str, promote: bool) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}], promote=promote)
    ingest(hub, [{"source": "default", "ref": "2"}], promote=promote)

    counting = _wire_counting_facts(hub)

    resp = hub.client.get(path)

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["entries"]) == 2
    # The whole request — ordering, candidate filtering, and blocked marking alike —
    # derives the fleet's statuses exactly once, and never falls back to `load_all_facts`
    # or a per-chunk `load_facts`.
    assert counting.load_all_statuses_calls == 1
    assert counting.load_all_facts_calls == 0
    assert counting.load_facts_calls == 0


@pytest.mark.parametrize("path", ["/api/queue", "/api/backlog"])
def test_replace_issues_one_load_all_statuses_call_on_success(tmp_path: Path, path: str) -> None:
    promote = path == "/api/queue"
    hub = build_hub(tmp_path)
    a = ingest(hub, [{"source": "default", "ref": "1"}], promote=promote)
    b = ingest(hub, [{"source": "default", "ref": "2"}], promote=promote)

    counting = _wire_counting_facts(hub)

    resp = hub.client.put(path, json={"chunk_ids": [b, a]})

    assert resp.status_code == 200, resp.text
    assert counting.load_all_statuses_calls == 1
    assert counting.load_all_facts_calls == 0


@pytest.mark.parametrize("path", ["/api/queue", "/api/backlog"])
def test_replace_issues_one_load_all_statuses_call_on_conflict(tmp_path: Path, path: str) -> None:
    promote = path == "/api/queue"
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}], promote=promote)

    counting = _wire_counting_facts(hub)

    resp = hub.client.put(path, json={"chunk_ids": ["ch_not_a_member"]})

    assert resp.status_code == 409, resp.text
    assert counting.load_all_statuses_calls == 1
    assert counting.load_all_facts_calls == 0


@pytest.mark.parametrize("path", ["/api/queue/position", "/api/backlog/position"])
def test_reposition_issues_one_load_all_statuses_call_on_success(tmp_path: Path, path: str) -> None:
    promote = path == "/api/queue/position"
    hub = build_hub(tmp_path)
    a = ingest(hub, [{"source": "default", "ref": "1"}], promote=promote)
    b = ingest(hub, [{"source": "default", "ref": "2"}], promote=promote)

    counting = _wire_counting_facts(hub)

    resp = hub.client.post(path, json={"chunk_id": b, "after_chunk_id": None if a == b else a})

    assert resp.status_code == 200, resp.text
    assert counting.load_all_statuses_calls == 1
    assert counting.load_all_facts_calls == 0


@pytest.mark.parametrize("path", ["/api/queue/position", "/api/backlog/position"])
def test_reposition_issues_one_load_all_statuses_call_on_conflict(tmp_path: Path, path: str) -> None:
    promote = path == "/api/queue/position"
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}], promote=promote)

    counting = _wire_counting_facts(hub)

    resp = hub.client.post(path, json={"chunk_id": "ch_not_a_member", "after_chunk_id": None})

    assert resp.status_code == 409, resp.text
    assert counting.load_all_statuses_calls == 1
    assert counting.load_all_facts_calls == 0


@pytest.mark.parametrize(("path", "promote"), _PEEKS)
def test_whole_order_replace_writes_every_position_in_one_transaction_regardless_of_length(
    tmp_path: Path, path: str, promote: bool
) -> None:
    """A whole-order replace of N chunks costs one write transaction, not N — the
    statement count must not grow with the queue's length."""
    replace_path = "/api/queue" if path == "/api/queue" else "/api/backlog"
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    small_ids = [ingest(small, [{"source": "default", "ref": str(i)}], promote=promote) for i in range(3)]
    large = build_hub(tmp_path / "large")
    large_ids = [ingest(large, [{"source": "default", "ref": str(i)}], promote=promote) for i in range(9)]

    def call(hub, ids: list[str]) -> None:  # type: ignore[no-untyped-def]
        resp = hub.client.put(replace_path, json={"chunk_ids": list(reversed(ids))})
        assert resp.status_code == 200, resp.text

    small_count = count_queries(small.engine, lambda: call(small, small_ids))
    large_count = count_queries(large.engine, lambda: call(large, large_ids))

    assert small_count == large_count
