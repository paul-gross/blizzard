"""``HubWorkSource`` — the built-in, always-seated work source (issue #357, component
tier). Its "fetch" is a read of this hub's own durable store, not a forge round-trip;
see ``tests/test_work_source.py`` for the pass-through (GitHub) binding's sibling proof.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import WorkItemAuthor, WorkItemClosure, WorkItemPriority, WorkRef
from blizzard.hub.domain.work_items import (
    WorkItemEdit,
    WorkItemEditService,
    WorkItemHeldByLiveChunk,
    WorkItemNotEditable,
)
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.work_sources.editor import WorkItemRefUnknownError
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.source import WorkSourceError
from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _source(tmp_path: Path) -> tuple[HubWorkSource, WorkItemStore, ChunkStore, Engine, FixedClock]:
    _, engine = migrate_to(tmp_path, "head")
    items = WorkItemStore(engine)
    clock = FixedClock(_T0)
    chunks = ChunkStore(engine, clock)
    edits = WorkItemEditService(items=items, chunks=chunks, clock=clock)
    return HubWorkSource(items, chunks, edits), items, chunks, engine, clock


def test_parse_claims_the_reserved_colon_token_form(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)
    pointer = source.parse("hub:42")
    assert pointer == WorkRef(source="hub", ref="42")


def test_parse_rejects_a_token_shaped_for_another_source(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)
    assert source.parse("widget:42") is None
    assert source.parse("hub#42") is None
    assert source.parse("no-separator") is None


def test_label_renders_the_reserved_name_colon_ref(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)
    assert source.label(WorkRef(source="hub", ref="42")) == "hub:42"


def test_branch_url_is_always_none(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)
    assert source.branch_url("acme/widget", "feat/x") is None


def test_fetch_reads_an_open_item_s_title_and_body(tmp_path: Path) -> None:
    source, items, _, _, _ = _source(tmp_path)
    created = items.create(
        source="hub",
        title="widget is broken",
        body="steps to repro",
        author=WorkItemAuthor.fleet(),
        stated_priority=None,
        at=_T0,
    )

    item = source.fetch(WorkRef(source="hub", ref=created.ref))

    assert item.title == "widget is broken"
    assert item.body == "steps to repro"
    assert item.comments == []


def test_fetch_an_unknown_ref_raises(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)
    with pytest.raises(WorkSourceError):
        source.fetch(WorkRef(source="hub", ref="999"))


def test_fetch_a_withdrawn_ref_raises(tmp_path: Path) -> None:
    source, items, _, _, _ = _source(tmp_path)
    created = items.create(
        source="hub", title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    items.close("hub", created.ref, closure=WorkItemClosure.WITHDRAWN, at=_T0)

    with pytest.raises(WorkSourceError):
        source.fetch(WorkRef(source="hub", ref=created.ref))


def test_fetch_a_delivered_ref_still_resolves(tmp_path: Path) -> None:
    """A closed-by-delivery item stays fetchable — only ``withdrawn`` is unresolvable."""
    source, items, _, _, _ = _source(tmp_path)
    created = items.create(
        source="hub", title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    items.close("hub", created.ref, closure=WorkItemClosure.DELIVERED, at=_T0)

    item = source.fetch(WorkRef(source="hub", ref=created.ref))
    assert item.body == "b"


def test_web_url_resolves_to_the_live_holder_s_board_chunk_link(tmp_path: Path) -> None:
    source, items, chunks, engine, _ = _source(tmp_path)
    created = items.create(
        source="hub", title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    pointer = WorkRef(source="hub", ref=created.ref)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
    chunks.add_work_refs("ch_1", [pointer], at=_T0)

    assert source.web_url(pointer) == "/board/chunk/ch_1"


def test_web_url_is_none_when_no_live_chunk_holds_the_pointer(tmp_path: Path) -> None:
    source, items, _, _, _ = _source(tmp_path)
    created = items.create(
        source="hub", title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    assert source.web_url(WorkRef(source="hub", ref=created.ref)) is None


# --------------------------------------------------------------------------- #
# IWorkEditor (blizzard#358) — list/get/create/edit/withdraw


def test_list_and_get_answer_the_full_record_for_open_and_withdrawn_items(tmp_path: Path) -> None:
    source, items, _, _, _ = _source(tmp_path)
    open_item = items.create(
        source="hub", title="open", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    withdrawn = items.create(
        source="hub", title="withdrawn", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    items.close("hub", withdrawn.ref, closure=WorkItemClosure.WITHDRAWN, at=_T0)

    listed = source.list()

    assert {item.work_item_id for item in listed} == {open_item.work_item_id, withdrawn.work_item_id}
    assert source.get(WorkRef(source="hub", ref=open_item.ref)).title == "open"
    assert source.get(WorkRef(source="hub", ref=withdrawn.ref)).closure == WorkItemClosure.WITHDRAWN


def test_get_edit_and_withdraw_of_an_unallocated_ref_raise_not_found(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)
    pointer = WorkRef(source="hub", ref="999")

    with pytest.raises(WorkItemRefUnknownError):
        source.get(pointer)
    with pytest.raises(WorkItemRefUnknownError):
        source.edit(pointer, WorkItemEdit(title="t", body="b", stated_priority=None))
    with pytest.raises(WorkItemRefUnknownError):
        source.withdraw(pointer)


def test_create_allocates_an_open_item(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)

    created = source.create(title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=WorkItemPriority.HIGH)

    assert created.title == "t"
    assert created.closure is None
    assert source.get(WorkRef(source="hub", ref=created.ref)) == created


def test_edit_replaces_fields_and_stamps_edited_at_leaving_created_at_and_ref(tmp_path: Path) -> None:
    source, _, _, _, clock = _source(tmp_path)
    created = source.create(title="before", body="before", author=WorkItemAuthor.fleet(), stated_priority=None)
    pointer = WorkRef(source="hub", ref=created.ref)
    clock.advance(timedelta(days=1))

    edited = source.edit(pointer, WorkItemEdit(title="after", body="after", stated_priority=WorkItemPriority.HIGH))

    assert edited.title == "after"
    assert edited.body == "after"
    assert edited.stated_priority == "high"
    assert edited.edited_at == clock.instant
    assert edited.created_at == created.created_at
    assert edited.ref == created.ref


def test_withdraw_sets_the_withdrawn_closure(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)
    created = source.create(title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None)

    withdrawn = source.withdraw(WorkRef(source="hub", ref=created.ref))

    assert withdrawn.closure == WorkItemClosure.WITHDRAWN
    assert withdrawn.closed_at is not None


def test_edit_and_withdraw_of_a_closed_item_are_refused(tmp_path: Path) -> None:
    source, _, _, _, _ = _source(tmp_path)
    created = source.create(title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None)
    pointer = WorkRef(source="hub", ref=created.ref)
    source.withdraw(pointer)

    with pytest.raises(WorkItemNotEditable):
        source.edit(pointer, WorkItemEdit(title="t2", body="b2", stated_priority=None))
    with pytest.raises(WorkItemNotEditable):
        source.withdraw(pointer)


def test_withdraw_is_refused_while_a_live_chunk_holds_the_ref(tmp_path: Path) -> None:
    source, _, chunks, engine, _ = _source(tmp_path)
    created = source.create(title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None)
    pointer = WorkRef(source="hub", ref=created.ref)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
    chunks.add_work_refs("ch_1", [pointer], at=_T0)

    with pytest.raises(WorkItemHeldByLiveChunk):
        source.withdraw(pointer)


def test_withdraw_succeeds_once_the_holding_chunk_is_no_longer_live(tmp_path: Path) -> None:
    source, _, chunks, engine, _ = _source(tmp_path)
    created = source.create(title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None)
    pointer = WorkRef(source="hub", ref=created.ref)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
    chunks.add_work_refs("ch_1", [pointer], at=_T0)
    chunks.record_stop("ch_1", by="operator", at=_T0)

    withdrawn = source.withdraw(pointer)

    assert withdrawn.closure == WorkItemClosure.WITHDRAWN
