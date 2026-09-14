"""``WorkItemStore.get_many`` — ``get``'s batched sibling (component tier).

Proves the batch read matches per-pointer ``get`` across two sources, an unknown ref
within a known source, and a source never seen at all; and that it is correct across a
lowered ``BATCH_SIZE`` boundary within one source."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.work import Chunk, WorkItemAuthor, WorkItemRecord, WorkRef
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal import batching as batching_module
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import hub_store_connections, seed_graph, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_engine(tmp_path: Path) -> tuple[WorkItemStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
    return WorkItemStore(hub_store_connections(engine)), engine


def _seed(store: WorkItemStore, *, source: str, chunk_id: str) -> WorkItemRecord:
    """``seed_work_item``'s shape, with an explicit ``chunk_id`` — its own ``ch_{ref}``
    naming collides across sources, since each source's ``ref`` allocation starts at 1
    independently."""
    ref = store.allocate_ref(source)
    pointer = WorkRef(source=source, ref=ref)
    chunk = Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[pointer], minted_at=_NOW)
    return store.create_with_chunk(
        pointer=pointer,
        title="t",
        body="b",
        author=WorkItemAuthor.user("usr_1"),
        stated_priority=None,
        at=_NOW,
        chunk=chunk,
    )


def test_get_many_matches_get_across_two_sources_an_unknown_ref_and_an_unseen_source(tmp_path: Path) -> None:
    store, _ = _store_and_engine(tmp_path)
    hub_item = _seed(store, source="hub", chunk_id="ch_hub_1")
    forge_item = _seed(store, source="forge", chunk_id="ch_forge_1")

    result = store.get_many(
        [
            WorkRef(source="hub", ref=hub_item.ref),
            WorkRef(source="forge", ref=forge_item.ref),
            WorkRef(source="hub", ref="never_allocated"),
            WorkRef(source="never_seen", ref="1"),
        ]
    )

    assert set(result) == {WorkRef(source="hub", ref=hub_item.ref), WorkRef(source="forge", ref=forge_item.ref)}
    for pointer, record in result.items():
        assert record == store.get(pointer.source, pointer.ref)


def test_get_many_of_no_pointers_is_empty(tmp_path: Path) -> None:
    store, _ = _store_and_engine(tmp_path)
    assert store.get_many([]) == {}


def test_get_many_matches_across_a_batch_boundary_within_one_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store_and_engine(tmp_path)
    items = [
        seed_work_item(store, source="hub", graph_id="gr_1", author=WorkItemAuthor.user("usr_1"), at=_NOW)
        for _ in range(7)
    ]

    result = store.get_many([WorkRef(source="hub", ref=item.ref) for item in items])

    assert set(result) == {WorkRef(source="hub", ref=item.ref) for item in items}
    for pointer, record in result.items():
        assert record == store.get(pointer.source, pointer.ref)
