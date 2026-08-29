"""``GET /api/chunks`` — the bulk-read list path (component tier, blizzard#421).

`list_chunks` used to fan `ChunkView.of`'s `load_facts` and `route_of` out once per chunk
(~29 queries per chunk at fleet scale). Proves the route now reads the fleet's facts and
routes with one bulk query each, so the query count is unchanged as fleet size grows, and
never reaches `load_facts`/`route_of` at all — the `FleetPulse.view()` shape (issue #374),
extended to the rendered list row."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import Engine, event

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import ChunkFacts
from blizzard.hub.store.internal.chunk_store import ChunkStore
from tests.support import build_hub, ingest

pytestmark = pytest.mark.component


def _count_queries(engine: Engine, fn) -> int:  # type: ignore[no-untyped-def]
    count = {"n": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        count["n"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    return count["n"]


def _seed(hub, n: int) -> None:  # type: ignore[no-untyped-def]
    for i in range(n):
        ingest(hub, [{"source": "default", "ref": str(i)}])


def test_list_chunks_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    _seed(small, 3)
    large = build_hub(tmp_path / "large")
    _seed(large, 9)  # 3x the small fleet

    results: dict[str, int] = {}

    def call(hub, key: str) -> None:  # type: ignore[no-untyped-def]
        resp = hub.client.get("/api/chunks")
        assert resp.status_code == 200, resp.text
        results[key] = len(resp.json())

    small_count = _count_queries(small.engine, lambda: call(small, "small"))
    large_count = _count_queries(large.engine, lambda: call(large, "large"))

    assert results == {"small": 3, "large": 9}
    assert small_count == large_count


class _CountingChunkStore(ChunkStore):
    """Counts calls to the bulk and per-chunk read seams, so a test can pin which shape
    `list_chunks` actually reaches — mirrors `test_load_all_facts_store`'s own
    `_CountingChunkStore`, extended to routes."""

    def __init__(self, engine: Engine, clock: IClock) -> None:
        super().__init__(engine, clock)
        self.load_all_facts_calls = 0
        self.load_all_routes_calls = 0
        self.load_facts_calls = 0
        self.route_of_calls = 0

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        self.load_all_facts_calls += 1
        return super().load_all_facts()

    def load_all_routes(self) -> dict[str, Route]:
        self.load_all_routes_calls += 1
        return super().load_all_routes()

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.load_facts_calls += 1
        return super().load_facts(chunk_id)

    def route_of(self, chunk_id: str) -> Route | None:
        self.route_of_calls += 1
        return super().route_of(chunk_id)


def test_list_chunks_calls_bulk_reads_and_never_load_facts_or_route_of(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    ingest(hub, [{"source": "default", "ref": "2"}])

    counting = _CountingChunkStore(hub.engine, hub.clock)
    assert hub.app is not None
    hub.app.state.services = replace(hub.services, chunks=counting)

    resp = hub.client.get("/api/chunks")

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2
    assert counting.load_all_facts_calls == 1
    assert counting.load_all_routes_calls == 1
    assert counting.load_facts_calls == 0
    assert counting.route_of_calls == 0
