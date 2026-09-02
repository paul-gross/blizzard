"""Two opposing dependency declarations racing resolve to exactly one commit (issue
#456, component tier).

``DependencyService`` shares its ``threading.Lock`` with ``ClaimService``, ``EditService``,
and ``RestartService`` (issue #120). These tests patch the store's write to pause mid-write,
proving an opposing declaration blocks on that lock rather than racing underneath it."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from blizzard.hub.domain.dependencies import DependencyWouldCloseCycle
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _writable_dependencies(hub: HubHarness) -> IWriteChunkDependenciesRepository:
    """These tests patch the chunk-dependencies store's write method to force the exact
    interleaving the shared lock must serialize."""
    return cast(IWriteChunkDependenciesRepository, hub.services.chunks.dependencies)


def test_two_opposing_declarations_racing_resolve_to_exactly_one_commit(tmp_path: Path) -> None:
    """``a`` depends on ``b`` and ``b`` depends on ``a``, declared concurrently: the second
    blocks on the shared lock until the first's write lands, then is refused for closing a
    cycle. Without the lock, both would see an empty standing set and both would write."""
    hub = build_hub(tmp_path)
    chunk_a = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    chunk_b = ingest(hub, [{"source": "default", "ref": "2"}], promote=False)
    a = hub.services.chunks.record.get(chunk_a)
    b = hub.services.chunks.record.get(chunk_b)
    assert a is not None
    assert b is not None

    entered_write = threading.Event()
    release_write = threading.Event()
    real_declare = _writable_dependencies(hub).declare

    def _blocking_declare(dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at):  # type: ignore[no-untyped-def]
        # Only the first declaration pauses — blocking both would pass even with the
        # shared lock removed (a surviving mutant, not a proof).
        if dependent_chunk_id == chunk_a:
            entered_write.set()
            assert release_write.wait(timeout=5), "test never released the first declaration's write"
        return real_declare(dependent_chunk_id, prerequisite_chunk_id, by=by, at=at)

    _writable_dependencies(hub).declare = _blocking_declare  # type: ignore[method-assign]

    first_result: dict[str, object] = {}

    def _declare_a_depends_on_b() -> None:
        first_result["edge"] = hub.services.dependencies.declare(a, b, by="user:alice")

    first_thread = threading.Thread(target=_declare_a_depends_on_b)
    first_thread.start()
    assert entered_write.wait(timeout=5), "the first declaration never reached its (patched) write"

    second_result: dict[str, object] = {}

    def _declare_b_depends_on_a() -> None:
        try:
            second_result["edge"] = hub.services.dependencies.declare(b, a, by="user:bob")
        except DependencyWouldCloseCycle as exc:
            second_result["refused"] = exc

    second_thread = threading.Thread(target=_declare_b_depends_on_a)
    second_thread.start()
    second_thread.join(timeout=0.3)
    assert second_thread.is_alive(), (
        "the opposing declaration completed while the first still held the shared lock — not atomic"
    )
    # Nothing has landed yet — the first declaration's write is still paused.
    assert hub.services.chunks.dependencies.list_standing_edges() == []

    release_write.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert "edge" in first_result, first_result
    assert "refused" in second_result, second_result
    assert isinstance(second_result["refused"], DependencyWouldCloseCycle)

    standing = hub.services.chunks.dependencies.list_standing_edges()
    assert len(standing) == 1
    assert standing[0].dependent_chunk_id == chunk_a
    assert standing[0].prerequisite_chunk_id == chunk_b


def test_repeated_opposing_declaration_races_never_yield_two_standing_edges(tmp_path: Path) -> None:
    """Many chunk pairs, each raced by two opposing declarations released together through
    a barrier — whichever side wins the shared lock, exactly one edge ever stands."""
    hub = build_hub(tmp_path)
    for i in range(8):
        chunk_x = ingest(hub, [{"source": "default", "ref": f"x{i}"}], promote=False)
        chunk_y = ingest(hub, [{"source": "default", "ref": f"y{i}"}], promote=False)
        x = hub.services.chunks.record.get(chunk_x)
        y = hub.services.chunks.record.get(chunk_y)
        assert x is not None
        assert y is not None
        start = threading.Barrier(2)
        results: dict[str, object] = {}

        def _x_depends_on_y(dep=x, prereq=y, barrier=start, sink=results) -> None:  # type: ignore[no-untyped-def]
            barrier.wait()
            try:
                sink["x_on_y"] = hub.services.dependencies.declare(dep, prereq, by="user:alice")
            except DependencyWouldCloseCycle:
                sink["x_on_y"] = "refused"

        def _y_depends_on_x(dep=y, prereq=x, barrier=start, sink=results) -> None:  # type: ignore[no-untyped-def]
            barrier.wait()
            try:
                sink["y_on_x"] = hub.services.dependencies.declare(dep, prereq, by="user:bob")
            except DependencyWouldCloseCycle:
                sink["y_on_x"] = "refused"

        threads = [threading.Thread(target=_x_depends_on_y), threading.Thread(target=_y_depends_on_x)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        outcomes = {results["x_on_y"] == "refused", results["y_on_x"] == "refused"}
        assert outcomes == {True, False}, f"pair {i}: {results}"
        standing = [
            e
            for e in hub.services.chunks.dependencies.list_standing_edges()
            if e.dependent_chunk_id in (chunk_x, chunk_y) and e.prerequisite_chunk_id in (chunk_x, chunk_y)
        ]
        assert len(standing) == 1, f"pair {i}: {standing}"
