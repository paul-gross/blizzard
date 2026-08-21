"""``WorkItemStore`` — the hub-owned work item repository (issue #357, component tier).

Exercises ``create_with_chunk``/``close``/``get`` through the read/write Protocol split
(``bzh:repository-split``): every write is read back through the read variant alone.
There is no chunkless filing path (blizzard#359) — a fixture here allocates a ref then
inserts item + chunk together, mirroring production's own two-step mint."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.work import Chunk, IReadWorkItemRepository, WorkItemAuthor, WorkItemClosure, WorkRef
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import seed_graph, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_engine(tmp_path: Path) -> tuple[WorkItemStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
    return WorkItemStore(engine), engine


def _store(tmp_path: Path) -> WorkItemStore:
    store, _ = _store_and_engine(tmp_path)
    return store


def test_get_of_an_unknown_ref_is_none(tmp_path: Path) -> None:
    reader: IReadWorkItemRepository = _store(tmp_path)
    assert reader.get("hub", "1") is None


def test_create_reads_back_open_with_no_closure(tmp_path: Path) -> None:
    store = _store(tmp_path)

    created = seed_work_item(
        store,
        graph_id="gr_1",
        title="widget is broken",
        body="steps to repro",
        author=WorkItemAuthor.user("usr_1"),
        stated_priority="high",
        at=_NOW,
    )

    reader: IReadWorkItemRepository = store
    fetched = reader.get("hub", created.ref)
    assert fetched is not None
    assert fetched.title == "widget is broken"
    assert fetched.body == "steps to repro"
    assert fetched.author == WorkItemAuthor.user("usr_1")
    assert fetched.stated_priority == "high"
    assert fetched.closed_at is None
    assert fetched.closure is None


def test_ref_allocation_is_monotonic_and_never_reused(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = seed_work_item(
        store, graph_id="gr_1", title="a", body="a", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
    )
    second = seed_work_item(
        store, graph_id="gr_1", title="b", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
    )

    assert first.ref != second.ref
    assert int(second.ref) > int(first.ref)


def test_list_breaks_a_same_instant_created_at_tie_on_work_item_id(tmp_path: Path) -> None:
    """Two items created at the identical instant still sort deterministically — not an
    artifact of sqlite's rowid-order fallback, which a real (postgres) engine wouldn't
    give a bare ``created_at`` ordering."""
    store = _store(tmp_path)
    first = seed_work_item(
        store, graph_id="gr_1", title="a", body="a", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
    )
    second = seed_work_item(
        store, graph_id="gr_1", title="b", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
    )

    expected_order = sorted([first.work_item_id, second.work_item_id], reverse=True)
    assert [item.work_item_id for item in store.list("hub")] == expected_order
    assert [item.work_item_id for item in store.list("hub")] == expected_order  # stable across repeated reads


def test_list_respects_the_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i in range(3):
        seed_work_item(
            store, graph_id="gr_1", title=str(i), body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
        )

    assert len(store.list("hub", limit=2)) == 2


def test_close_is_unset_until_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = seed_work_item(
        store, graph_id="gr_1", title="a", body="a", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
    )
    assert store.get("hub", created.ref).closed_at is None  # type: ignore[union-attr]

    closed_at = datetime(2026, 7, 16, 12, 5, 0, tzinfo=UTC)
    closed = store.close("hub", created.ref, closure=WorkItemClosure.WITHDRAWN, at=closed_at)

    assert closed.closed_at == closed_at
    assert closed.closure == WorkItemClosure.WITHDRAWN
    fetched = store.get("hub", created.ref)
    assert fetched is not None
    assert fetched.closed_at == closed_at
    assert fetched.closure == WorkItemClosure.WITHDRAWN


def test_edit_of_a_closed_item_is_a_no_op_and_returns_none(tmp_path: Path) -> None:
    """The ``closed_at IS NULL`` guard mirrors ``close``'s own (:107) — a title/body
    write racing a closure matches zero rows rather than landing on a closed item."""
    store = _store(tmp_path)
    created = seed_work_item(
        store, graph_id="gr_1", title="a", body="a", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
    )
    store.close("hub", created.ref, closure=WorkItemClosure.WITHDRAWN, at=_NOW)

    result = store.edit("hub", created.ref, title="changed", body="changed", stated_priority=None, at=_NOW)

    assert result is None
    fetched = store.get("hub", created.ref)
    assert fetched is not None
    assert fetched.title == "a"  # untouched


# --------------------------------------------------------------------------- #
# ``create_with_chunk`` — the composite write (blizzard#359)


def test_create_with_chunk_inserts_the_item_and_the_chunk_rows_together(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    pointer = WorkRef(source="hub", ref="1")
    chunk = Chunk(chunk_id="ch_1", graph_id="gr_1", work_refs=[pointer], minted_at=_NOW)

    created = store.create_with_chunk(
        pointer=pointer,
        title="widget is broken",
        body="steps to repro",
        author=WorkItemAuthor.fleet(),
        stated_priority=None,
        at=_NOW,
        chunk=chunk,
    )

    assert created.source == "hub"
    assert created.ref == "1"
    fetched = store.get("hub", "1")
    assert fetched is not None
    assert fetched.title == "widget is broken"

    with engine.begin() as conn:
        chunk_rows = conn.execute(select(s.chunks)).all()
        assert [row.chunk_id for row in chunk_rows] == ["ch_1"]
        assert chunk_rows[0].graph_id == "gr_1"
        ref_rows = conn.execute(select(s.chunk_work_refs)).all()
        assert [(row.chunk_id, row.source, row.ref) for row in ref_rows] == [("ch_1", "hub", "1")]


def test_create_with_chunk_rolls_back_the_item_and_the_chunk_rows_on_a_failing_chunk_write(tmp_path: Path) -> None:
    """A store failure inside the composite write leaves no row durable — proven against
    the real engine, since a rollback is not observable through a seam double."""
    store, engine = _store_and_engine(tmp_path)
    # The second pointer's NOT NULL `ref` fails the write only once the `chunks` row has
    # landed, so the rollback under test covers all three tables, not the item row alone.
    pointer = WorkRef(source="hub", ref="1")
    bad = WorkRef(source="hub", ref=cast(str, None))
    chunk = Chunk(chunk_id="ch_1", graph_id="gr_1", work_refs=[pointer, bad], minted_at=_NOW)

    with pytest.raises(IntegrityError):
        store.create_with_chunk(
            pointer=pointer,
            title="t",
            body="b",
            author=WorkItemAuthor.fleet(),
            stated_priority=None,
            at=_NOW,
            chunk=chunk,
        )

    assert store.get("hub", "1") is None
    with engine.begin() as conn:
        assert conn.execute(select(s.chunks)).all() == []
        assert conn.execute(select(s.chunk_work_refs)).all() == []


def test_allocate_ref_is_monotonic_and_never_reused(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.allocate_ref("hub")
    second = store.allocate_ref("hub")

    assert first != second
    assert int(second) > int(first)
