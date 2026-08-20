"""``HubWorkSource`` — the built-in, always-seated work source (issue #357, component
tier). Its "fetch" is a read of this hub's own durable store, not a forge round-trip;
see ``tests/test_work_source.py`` for the pass-through (GitHub) binding's sibling proof.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import WorkItemAuthor, WorkItemClosure, WorkRef
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.source import WorkSourceError
from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _source(tmp_path: Path) -> tuple[HubWorkSource, WorkItemStore, ChunkStore, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    items = WorkItemStore(engine)
    chunks = ChunkStore(engine, FixedClock(_T0))
    return HubWorkSource(items, chunks), items, chunks, engine


def test_parse_claims_the_reserved_colon_token_form(tmp_path: Path) -> None:
    source, _, _, _ = _source(tmp_path)
    pointer = source.parse("hub:42")
    assert pointer == WorkRef(source="hub", ref="42")


def test_parse_rejects_a_token_shaped_for_another_source(tmp_path: Path) -> None:
    source, _, _, _ = _source(tmp_path)
    assert source.parse("widget:42") is None
    assert source.parse("hub#42") is None
    assert source.parse("no-separator") is None


def test_label_renders_the_reserved_name_colon_ref(tmp_path: Path) -> None:
    source, _, _, _ = _source(tmp_path)
    assert source.label(WorkRef(source="hub", ref="42")) == "hub:42"


def test_branch_url_is_always_none(tmp_path: Path) -> None:
    source, _, _, _ = _source(tmp_path)
    assert source.branch_url("acme/widget", "feat/x") is None


def test_fetch_reads_an_open_item_s_title_and_body(tmp_path: Path) -> None:
    source, items, _, _ = _source(tmp_path)
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
    source, _, _, _ = _source(tmp_path)
    with pytest.raises(WorkSourceError):
        source.fetch(WorkRef(source="hub", ref="999"))


def test_fetch_a_withdrawn_ref_raises(tmp_path: Path) -> None:
    source, items, _, _ = _source(tmp_path)
    created = items.create(
        source="hub", title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    items.close("hub", created.ref, closure=WorkItemClosure.WITHDRAWN, at=_T0)

    with pytest.raises(WorkSourceError):
        source.fetch(WorkRef(source="hub", ref=created.ref))


def test_fetch_a_delivered_ref_still_resolves(tmp_path: Path) -> None:
    """A closed-by-delivery item stays fetchable — only ``withdrawn`` is unresolvable."""
    source, items, _, _ = _source(tmp_path)
    created = items.create(
        source="hub", title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    items.close("hub", created.ref, closure=WorkItemClosure.DELIVERED, at=_T0)

    item = source.fetch(WorkRef(source="hub", ref=created.ref))
    assert item.body == "b"


def test_web_url_resolves_to_the_live_holder_s_board_chunk_link(tmp_path: Path) -> None:
    source, items, chunks, engine = _source(tmp_path)
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
    source, items, _, _ = _source(tmp_path)
    created = items.create(
        source="hub", title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_T0
    )
    assert source.web_url(WorkRef(source="hub", ref=created.ref)) is None
