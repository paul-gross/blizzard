"""``HubWorkSource`` — the built-in, always-seated work source (issue #357, component
tier). Its "fetch" is a read of this hub's own durable store, not a forge round-trip;
see ``tests/test_work_source.py`` for the pass-through (GitHub) binding's sibling proof.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import USER_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.models import User
from blizzard.hub.domain.delete import DeleteService
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import WorkItemAuthor, WorkItemClosure, WorkItemPriority, WorkRef
from blizzard.hub.domain.work_items import (
    WorkItemEdit,
    WorkItemEditService,
    WorkItemHeldByLiveChunk,
    WorkItemNotEditable,
)
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.work_sources.closer import WorkItemGoneError
from blizzard.hub.work_sources.editor import WorkItemRefUnknownError
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.source import WorkSourceError
from tests.support import migrate_to, seed_chunk, seed_graph, seed_work_item

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _source(tmp_path: Path) -> tuple[HubWorkSource, WorkItemStore, ChunkStore, UserRepository, Engine, FixedClock]:
    _, engine = migrate_to(tmp_path, "head")
    items = WorkItemStore(engine)
    clock = FixedClock(_T0)
    chunks = ChunkStore(engine, clock)
    delete = DeleteService(chunks=chunks, items=items, clock=clock)
    edits = WorkItemEditService(items=items, chunks=chunks, clock=clock, delete=delete)
    users = UserRepository(engine, RepoErrorFactory(get_logger("tests.test_hub_work_source")))
    return HubWorkSource(items, chunks, edits, users), items, chunks, users, engine, clock


def _user(users: UserRepository, *, username: str) -> User:
    user = User(
        user_id=Id.mint_at(USER_PREFIX, _T0).value,
        username=username,
        display_name=username,
        email=None,
        role=Role.CONTRIBUTOR,
        created_at=_T0,
    )
    users.create(user)
    return user


def _graph(engine: Engine) -> Graph:
    """A minimal graph a minted chunk can pin to — a seeded row for the FK,
    ``create``'s own ``graph`` parameter needing only the id (blizzard#359)."""
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return Graph(graph_id="gr_1", name="g", entry_node_id="nd_1", nodes=[], edges=[], created_at=_T0)


def test_parse_claims_the_reserved_colon_token_form(tmp_path: Path) -> None:
    source, _, _, _, _, _ = _source(tmp_path)
    pointer = source.parse("hub:42")
    assert pointer == WorkRef(source="hub", ref="42")


def test_parse_rejects_a_token_shaped_for_another_source(tmp_path: Path) -> None:
    source, _, _, _, _, _ = _source(tmp_path)
    assert source.parse("widget:42") is None
    assert source.parse("hub#42") is None
    assert source.parse("no-separator") is None


def test_label_renders_the_reserved_name_colon_ref(tmp_path: Path) -> None:
    source, _, _, _, _, _ = _source(tmp_path)
    assert source.label(WorkRef(source="hub", ref="42")) == "hub:42"


def test_branch_url_is_always_none(tmp_path: Path) -> None:
    source, _, _, _, _, _ = _source(tmp_path)
    assert source.branch_url("acme/widget", "feat/x") is None


def test_fetch_reads_an_open_item_s_title_and_body(tmp_path: Path) -> None:
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    created = seed_work_item(
        items,
        graph_id=graph.graph_id,
        title="widget is broken",
        body="steps to repro",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_T0,
    )

    item = source.fetch(WorkRef(source="hub", ref=created.ref))

    assert item.title == "widget is broken"
    assert item.body == "steps to repro"
    assert item.comments == []


def test_fetch_resolves_a_user_author_s_login(tmp_path: Path) -> None:
    """The one place a bare ``user_id`` is ever resolved to a login (blizzard#362)."""
    source, items, _, users, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    user = _user(users, username="alice")
    created = seed_work_item(items, graph_id=graph.graph_id, author=WorkItemAuthor.user(user.user_id), at=_T0)

    item = source.fetch(WorkRef(source="hub", ref=created.ref))

    assert item.author is not None
    assert item.author.kind == "user"
    assert item.author.user_id == user.user_id
    assert item.author.login == "alice"


def test_fetch_names_a_fleet_author_s_runner_chunk_and_node(tmp_path: Path) -> None:
    """No route mints a fleet author — proven from a directly-seeded store row."""
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    author = WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_proposer", node_name="triage")
    created = seed_work_item(items, graph_id=graph.graph_id, author=author, at=_T0)

    item = source.fetch(WorkRef(source="hub", ref=created.ref))

    assert item.author is not None
    assert item.author.kind == "fleet"
    assert item.author.runner_id == "runner-local"
    assert item.author.chunk_id == "ch_proposer"
    assert item.author.node_name == "triage"
    assert item.author.user_id is None
    assert item.author.login is None


def test_fetch_carries_an_absent_stated_priority_through(tmp_path: Path) -> None:
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    created = seed_work_item(
        items,
        graph_id=graph.graph_id,
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        at=_T0,
    )

    item = source.fetch(WorkRef(source="hub", ref=created.ref))

    assert item.stated_priority is None


def test_fetch_carries_a_set_stated_priority_through(tmp_path: Path) -> None:
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    created = seed_work_item(
        items,
        graph_id=graph.graph_id,
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=WorkItemPriority.HIGH.value,
        at=_T0,
    )

    item = source.fetch(WorkRef(source="hub", ref=created.ref))

    assert item.stated_priority == "high"


def test_fetch_an_unknown_ref_raises(tmp_path: Path) -> None:
    source, _, _, _, _, _ = _source(tmp_path)
    with pytest.raises(WorkSourceError):
        source.fetch(WorkRef(source="hub", ref="999"))


def test_fetch_a_withdrawn_ref_raises(tmp_path: Path) -> None:
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    created = seed_work_item(
        items,
        graph_id=graph.graph_id,
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_T0,
    )
    items.close("hub", created.ref, closure=WorkItemClosure.WITHDRAWN, at=_T0)

    with pytest.raises(WorkSourceError):
        source.fetch(WorkRef(source="hub", ref=created.ref))


def test_fetch_a_delivered_ref_still_resolves(tmp_path: Path) -> None:
    """A closed-by-delivery item stays fetchable — only ``withdrawn`` is unresolvable."""
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    created = seed_work_item(
        items,
        graph_id=graph.graph_id,
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_T0,
    )
    items.close("hub", created.ref, closure=WorkItemClosure.DELIVERED, at=_T0)

    item = source.fetch(WorkRef(source="hub", ref=created.ref))
    assert item.body == "b"


def test_web_url_resolves_to_the_live_holder_s_board_chunk_link(tmp_path: Path) -> None:
    """``web_url`` reads only ``find_live_holder`` — no item row is needed to prove it."""
    source, _, chunks, _, engine, _ = _source(tmp_path)
    pointer = WorkRef(source="hub", ref="1")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_T0)
    chunks.add_work_refs("ch_1", [pointer], at=_T0)

    assert source.web_url(pointer) == "/board/chunk/ch_1"


def test_web_url_is_none_when_no_live_chunk_holds_the_pointer(tmp_path: Path) -> None:
    source, _, _, _, _, _ = _source(tmp_path)
    assert source.web_url(WorkRef(source="hub", ref="1")) is None


# --------------------------------------------------------------------------- #
# IWorkEditor (blizzard#358) — list/get/create/edit/withdraw


def test_list_and_get_answer_the_full_record_for_open_and_withdrawn_items(tmp_path: Path) -> None:
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    open_item = seed_work_item(
        items,
        graph_id=graph.graph_id,
        title="open",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_T0,
    )
    withdrawn = seed_work_item(
        items,
        graph_id=graph.graph_id,
        title="withdrawn",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_T0,
    )
    items.close("hub", withdrawn.ref, closure=WorkItemClosure.WITHDRAWN, at=_T0)

    listed = source.list()

    assert {item.work_item_id for item in listed} == {open_item.work_item_id, withdrawn.work_item_id}
    assert source.get(WorkRef(source="hub", ref=open_item.ref)).title == "open"
    assert source.get(WorkRef(source="hub", ref=withdrawn.ref)).closure == WorkItemClosure.WITHDRAWN


def test_get_edit_and_withdraw_of_an_unallocated_ref_raise_not_found(tmp_path: Path) -> None:
    source, _, _, _, _, _ = _source(tmp_path)
    pointer = WorkRef(source="hub", ref="999")

    with pytest.raises(WorkItemRefUnknownError):
        source.get(pointer)
    with pytest.raises(WorkItemRefUnknownError):
        source.edit(pointer, WorkItemEdit(title="t", body="b", stated_priority=None))
    with pytest.raises(WorkItemRefUnknownError):
        source.withdraw(pointer)


def test_create_allocates_an_open_item(tmp_path: Path) -> None:
    source, _, _, _, engine, _ = _source(tmp_path)

    created = source.create(
        title="t",
        body="b",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=WorkItemPriority.HIGH,
        graph=_graph(engine),
    )

    assert created.item.title == "t"
    assert created.item.closure is None
    assert source.get(WorkRef(source="hub", ref=created.item.ref)) == created.item


def test_create_mints_a_not_ready_chunk_pinned_to_the_graph_and_holding_the_ref(tmp_path: Path) -> None:
    """The composite write's own claim (blizzard#359): one chunk, on the passed graph,
    holding exactly the pointer creation just allocated."""
    source, _, chunks, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)

    created = source.create(
        title="t",
        body="b",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        graph=graph,
    )

    chunk = chunks.get(created.chunk_id)
    assert chunk is not None
    assert chunk.graph_id == graph.graph_id
    assert chunk.work_refs == [WorkRef(source="hub", ref=created.item.ref)]
    facts = chunks.load_facts(created.chunk_id)
    assert facts is not None
    assert facts.status().value == "not_ready"


def test_edit_replaces_fields_and_stamps_edited_at_leaving_created_at_and_ref(tmp_path: Path) -> None:
    source, _, _, _, engine, clock = _source(tmp_path)
    created = source.create(
        title="before",
        body="before",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        graph=_graph(engine),
    )
    pointer = WorkRef(source="hub", ref=created.item.ref)
    clock.advance(timedelta(days=1))

    edited = source.edit(pointer, WorkItemEdit(title="after", body="after", stated_priority=WorkItemPriority.HIGH))

    assert edited.title == "after"
    assert edited.body == "after"
    assert edited.stated_priority == "high"
    assert edited.edited_at == clock.instant
    assert edited.created_at == created.item.created_at
    assert edited.ref == created.item.ref


def test_withdraw_sets_the_withdrawn_closure(tmp_path: Path) -> None:
    source, _, chunks, _, engine, clock = _source(tmp_path)
    created = source.create(
        title="t",
        body="b",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        graph=_graph(engine),
    )
    chunks.record_stop(created.chunk_id, by="operator", at=clock.instant)

    withdrawn = source.withdraw(WorkRef(source="hub", ref=created.item.ref))

    assert withdrawn.closure == WorkItemClosure.WITHDRAWN
    assert withdrawn.closed_at is not None


def test_edit_and_withdraw_of_a_closed_item_are_refused(tmp_path: Path) -> None:
    source, _, chunks, _, engine, clock = _source(tmp_path)
    created = source.create(
        title="t",
        body="b",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        graph=_graph(engine),
    )
    pointer = WorkRef(source="hub", ref=created.item.ref)
    chunks.record_stop(created.chunk_id, by="operator", at=clock.instant)
    source.withdraw(pointer)

    with pytest.raises(WorkItemNotEditable):
        source.edit(pointer, WorkItemEdit(title="t2", body="b2", stated_priority=None))
    with pytest.raises(WorkItemNotEditable):
        source.withdraw(pointer)


def test_withdraw_deletes_an_unacquired_holder_and_withdraws_the_item(tmp_path: Path) -> None:
    """D3 (issue #364): a not_ready holder is unacquired, not genuinely live — withdraw
    deletes it rather than refusing. Creation itself mints the holder (blizzard#359)."""
    source, _, chunks, _, engine, _ = _source(tmp_path)
    created = source.create(
        title="t",
        body="b",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        graph=_graph(engine),
    )
    pointer = WorkRef(source="hub", ref=created.item.ref)

    withdrawn = source.withdraw(pointer)

    assert withdrawn.closure == WorkItemClosure.WITHDRAWN
    assert chunks.get(created.chunk_id) is None  # deleted along with the withdrawal


def test_withdraw_of_an_item_the_cascade_already_closed_is_refused(tmp_path: Path) -> None:
    """A second withdrawal after the delete cascade already closed ``item`` hits
    ``_require_open``'s existing ``closed_at IS NULL`` guard — no second write
    (blizzard#364, idempotent-by-guard)."""
    source, _, _, _, engine, _ = _source(tmp_path)
    created = source.create(
        title="t",
        body="b",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        graph=_graph(engine),
    )
    pointer = WorkRef(source="hub", ref=created.item.ref)
    source.withdraw(pointer)  # cascades: deletes the unacquired holder too

    with pytest.raises(WorkItemNotEditable):
        source.withdraw(pointer)


def test_withdraw_is_refused_while_an_acquired_chunk_holds_the_ref(tmp_path: Path) -> None:
    """A claimed (running) holder is genuinely acquired — outside
    ``GROUPABLE_STATUSES`` — so withdraw still refuses it exactly as before (D3)."""
    source, _, chunks, _, engine, clock = _source(tmp_path)
    created = source.create(
        title="t",
        body="b",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        graph=_graph(engine),
    )
    pointer = WorkRef(source="hub", ref=created.item.ref)
    chunks.record_route(
        Route(
            chunk_id=created.chunk_id, runner_id="r1", workspace_id="w1", environment_ids=[], created_at=clock.instant
        ),
        token_hash="deadbeef",
        at=clock.instant,
    )

    with pytest.raises(WorkItemHeldByLiveChunk) as excinfo:
        source.withdraw(pointer)
    assert excinfo.value.chunk_id == created.chunk_id


def test_withdraw_succeeds_once_the_holding_chunk_is_no_longer_live(tmp_path: Path) -> None:
    source, _, chunks, _, engine, clock = _source(tmp_path)
    created = source.create(
        title="t",
        body="b",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        stated_priority=None,
        graph=_graph(engine),
    )
    pointer = WorkRef(source="hub", ref=created.item.ref)
    chunks.record_stop(created.chunk_id, by="operator", at=clock.instant)

    withdrawn = source.withdraw(pointer)

    assert withdrawn.closure == WorkItemClosure.WITHDRAWN


# --------------------------------------------------------------------------- #
# IWorkCloser — the built-in hub source needs no `close = true` opt-in (issue #360)


def test_close_marks_an_open_item_delivered(tmp_path: Path) -> None:
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    created = seed_work_item(
        items,
        graph_id=graph.graph_id,
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_T0,
    )
    pointer = WorkRef(source="hub", ref=created.ref)

    source.close(pointer)

    row = items.get("hub", created.ref)
    assert row is not None
    assert row.closure is WorkItemClosure.DELIVERED
    assert row.closed_at is not None


def test_close_is_idempotent_on_an_already_delivered_item(tmp_path: Path) -> None:
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    created = seed_work_item(
        items,
        graph_id=graph.graph_id,
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_T0,
    )
    pointer = WorkRef(source="hub", ref=created.ref)

    source.close(pointer)
    source.close(pointer)  # must not raise — a clean no-op re-close

    row = items.get("hub", created.ref)
    assert row is not None
    assert row.closure is WorkItemClosure.DELIVERED


def test_close_a_missing_ref_raises_work_item_gone(tmp_path: Path) -> None:
    source, _, _, _, _, _ = _source(tmp_path)

    with pytest.raises(WorkItemGoneError):
        source.close(WorkRef(source="hub", ref="999"))


def test_close_leaves_a_withdrawn_item_withdrawn(tmp_path: Path) -> None:
    """A withdrawn item is never overwritten to ``delivered`` — the store's own
    ``closed_at IS NULL`` guard, not a branch in :meth:`HubWorkSource.close`."""
    source, items, _, _, engine, _ = _source(tmp_path)
    graph = _graph(engine)
    created = seed_work_item(
        items,
        graph_id=graph.graph_id,
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_T0,
    )
    items.close("hub", created.ref, closure=WorkItemClosure.WITHDRAWN, at=_T0)
    pointer = WorkRef(source="hub", ref=created.ref)

    source.close(pointer)  # must not raise, must not flip the closure

    row = items.get("hub", created.ref)
    assert row is not None
    assert row.closure is WorkItemClosure.WITHDRAWN
