"""``WorkItemStore.create_with_chunk_and_promote`` — a routine run's own one-act mint
(blizzard#392, component tier). Mirrors ``test_work_item_store.py``'s shape, plus the
promote-then-tail-stamp pair landing in the same transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.work import Chunk, WorkItemAuthor, WorkRef, mint_chunk
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import hub_store_connections, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _stores(tmp_path: Path) -> tuple[WorkItemStore, ChunkStore, RunContextStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
    conns = hub_store_connections(engine)
    return WorkItemStore(conns), ChunkStore(conns, FixedClock(_NOW)), RunContextStore(conns), engine


def test_item_lands_open_and_carries_the_runs_indexed_values(tmp_path: Path) -> None:
    items, _chunks, run_context, _engine = _stores(tmp_path)
    pointer = WorkRef(source="hub", ref=items.allocate_ref("hub"))
    chunk = mint_chunk([pointer], graph_id="gr_1", at=_NOW, default_model=["opus"], default_effort="high")

    _item, promoted_id = items.create_with_chunk_and_promote(
        pointer=pointer,
        title="gardening run (full)",
        body="Routine: gardening (graph: default)",
        author=WorkItemAuthor.user("usr_1"),
        routine_name="gardening",
        scope_slug="blizzard",
        run_mode="full",
        at=_NOW,
        chunk=chunk,
        position=0.0,
    )

    assert promoted_id is not None
    fetched = items.get("hub", pointer.ref)
    assert fetched is not None
    assert fetched.routine_name == "gardening"
    assert fetched.scope_slug == "blizzard"
    assert fetched.run_mode == "full"
    assert fetched.closed_at is None

    run = run_context.for_chunk(chunk)
    assert run is not None
    assert run.routine_name == "gardening"
    assert run.scope_slug == "blizzard"
    assert run.mode == "full"


def test_chunk_lands_ready_carrying_the_routines_defaults(tmp_path: Path) -> None:
    items, chunks, _run_context, _engine = _stores(tmp_path)
    pointer = WorkRef(source="hub", ref=items.allocate_ref("hub"))
    chunk = mint_chunk([pointer], graph_id="gr_1", at=_NOW, default_model=["opus"], default_effort="high")

    items.create_with_chunk_and_promote(
        pointer=pointer,
        title="t",
        body="b",
        author=WorkItemAuthor.user("usr_1"),
        routine_name="gardening",
        scope_slug="blizzard",
        run_mode="full",
        at=_NOW,
        chunk=chunk,
        position=0.0,
    )

    minted = chunks.get(chunk.chunk_id)
    assert minted is not None
    assert minted.default_model == ["opus"]
    assert minted.default_effort == "high"
    facts = chunks.load_facts(chunk.chunk_id)
    assert facts is not None
    assert facts.status() == ChunkStatus.READY
    assert chunks.queue_positions()[chunk.chunk_id] == 0.0


def test_tail_position_is_whatever_the_caller_computed(tmp_path: Path) -> None:
    """No re-derivation inside the store write — ``position`` is trusted as given,
    exactly as ``record_promote_with_tail_position`` already trusts its own caller."""
    items, chunks, _run_context, _engine = _stores(tmp_path)
    pointer = WorkRef(source="hub", ref=items.allocate_ref("hub"))
    chunk = mint_chunk([pointer], graph_id="gr_1", at=_NOW)

    items.create_with_chunk_and_promote(
        pointer=pointer,
        title="t",
        body="b",
        author=WorkItemAuthor.user("usr_1"),
        routine_name="gardening",
        scope_slug="blizzard",
        run_mode="full",
        at=_NOW,
        chunk=chunk,
        position=7.5,
    )

    assert chunks.queue_positions()[chunk.chunk_id] == 7.5


def test_a_failing_write_rolls_back_the_whole_composite(tmp_path: Path) -> None:
    """No window at all (``blizzard-context:/architecture/crash-correctness/hub.md``):
    a ``chunk_id`` collision fails the chunk-rows insert partway through the one
    transaction, and the item row it landed ahead of must not survive either."""
    items, chunks, run_context, engine = _stores(tmp_path)
    with engine.begin() as conn:
        seed_chunk(conn, "ch_collide", graph_id="gr_1", at=_NOW)
    pointer = WorkRef(source="hub", ref=items.allocate_ref("hub"))
    colliding_chunk = Chunk(chunk_id="ch_collide", graph_id="gr_1", work_refs=[pointer], minted_at=_NOW)

    with pytest.raises(HubStoreError):
        items.create_with_chunk_and_promote(
            pointer=pointer,
            title="t",
            body="b",
            author=WorkItemAuthor.user("usr_1"),
            routine_name="gardening",
            scope_slug="blizzard",
            run_mode="full",
            at=_NOW,
            chunk=colliding_chunk,
            position=0.0,
        )

    assert items.get("hub", pointer.ref) is None
    assert chunks.load_facts("ch_collide") is not None
    assert not chunks.load_facts("ch_collide").promoted  # type: ignore[union-attr]
    assert run_context.for_chunk(colliding_chunk) is None
