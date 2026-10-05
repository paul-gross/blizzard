"""Check-then-act decisions without a chunk row to lock are serialized (component tier).

Ingest, the default-graph mint, and promote each read their guard on the connection they
write on, under a lock (``bzh:store-exclusive-write``). The interleavings pause the first
writer inside its locked transaction, following ``tests/test_stop_claim_race.py``. Tests run
on SQLite, whose one writer lock serializes every writer — per-key non-serialization is
asserted structurally, on the lock rows written."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.operations.promote import ChunkNotPromotable
from blizzard.hub.store import schema as s
from tests.support import build_hub, ingest, pointer_token

pytestmark = pytest.mark.component


def _live_chunks_holding(hub, source: str, ref: str) -> list[str]:  # type: ignore[no-untyped-def]
    return [c.chunk_id for c in hub.services.chunks.record.list_all() if any(w.ref == ref for w in c.work_refs)]


def test_two_overlapping_ingests_of_one_pointer_mint_once(tmp_path: Path) -> None:
    """Pause the first ingest inside its locked transaction, after its guard read, and prove an
    overlapping ingest of the same pointer waits, then loses with the held-pointer 409."""
    hub = build_hub(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    record: Any = hub.services.chunks.record
    real_mint_locked = record.mint_locked

    def _blocking_mint_locked(handle, chunk):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(timeout=5), "test never released the first ingest"
        return real_mint_locked(handle, chunk)

    record.mint_locked = _blocking_mint_locked
    body = {"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    results: dict[str, Any] = {}

    def _ingest(name: str) -> None:
        results[name] = hub.client.post("/api/chunks", json=body)

    first = threading.Thread(target=_ingest, args=("first",))
    first.start()
    assert entered.wait(timeout=5), "first ingest never reached its locked mint"
    second = threading.Thread(target=_ingest, args=("second",))
    second.start()
    second.join(timeout=0.3)
    assert second.is_alive(), "the second ingest completed while the first held the pointer lock"

    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    record.mint_locked = real_mint_locked

    assert results["first"].status_code == 201, results["first"].text
    assert results["second"].status_code == 409, results["second"].text
    assert results["first"].json()["chunk_id"] in results["second"].text
    assert _live_chunks_holding(hub, "default", "1") == [results["first"].json()["chunk_id"]]


def test_ingests_of_different_pointers_lock_only_their_own_keys(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    ingest(hub, [{"source": "default", "ref": "2"}, {"source": "hub", "ref": "1"}], promote=False)

    with hub.engine.connect() as conn:
        rows = conn.execute(
            sa.select(s.keyed_locks.c.namespace, s.keyed_locks.c.key).where(s.keyed_locks.c.namespace == "work_ref")
        ).all()
    assert sorted(row.key for row in rows) == sorted(
        json.dumps(pair) for pair in (["default", "1"], ["default", "2"], ["hub", "1"])
    )


def test_two_concurrent_first_default_graph_resolutions_mint_it_once(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    services = hub.services
    graph_store: Any = services.graph_mint._graphs
    entered = threading.Event()
    release = threading.Event()
    real_mint_locked = graph_store.mint_locked

    def _blocking_mint_locked(handle, graph, *, definition_yaml, at):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(timeout=5), "test never released the first mint"
        return real_mint_locked(handle, graph, definition_yaml=definition_yaml, at=at)

    graph_store.mint_locked = _blocking_mint_locked
    ids: dict[str, str] = {}

    def _ensure(name: str) -> None:
        graph = services.graph_mint.ensure_default(
            services.default_graph_doc, definition_yaml=services.default_graph_yaml
        )
        ids[name] = graph.graph_id

    first = threading.Thread(target=_ensure, args=("first",))
    first.start()
    assert entered.wait(timeout=5), "first resolution never reached its locked mint"
    second = threading.Thread(target=_ensure, args=("second",))
    second.start()
    second.join(timeout=0.3)
    assert second.is_alive(), "the second resolution completed while the first held the name lock"

    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    graph_store.mint_locked = real_mint_locked

    assert ids["first"] == ids["second"]
    with hub.engine.connect() as conn:
        minted = conn.execute(
            sa.select(s.graphs.c.graph_id).where(s.graphs.c.name == services.default_graph_doc.name)
        ).all()
    assert [row.graph_id for row in minted] == [ids["first"]]


def test_a_chunk_stopped_between_promote_reads_and_its_locked_write_is_not_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop the chunk from inside the pre-lock tail computation — after the caller's reads,
    before the locked write — and prove promotability is judged from the facts read under the lock."""
    from blizzard.hub.domain.operations import promote as promote_module

    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    real_tail_position = promote_module.tail_position

    def _stopping_tail_position(*args, **kwargs):  # type: ignore[no-untyped-def]
        stopped = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})
        assert stopped.status_code == 202, stopped.text
        return real_tail_position(*args, **kwargs)

    monkeypatch.setattr(promote_module, "tail_position", _stopping_tail_position)

    with pytest.raises(ChunkNotPromotable):
        hub.services.promote.promote(chunk, statuses=hub.services.chunks.facts.load_live_statuses())

    with hub.engine.connect() as conn:
        assert conn.execute(sa.select(s.chunk_promoted).where(s.chunk_promoted.c.chunk_id == chunk_id)).first() is None
