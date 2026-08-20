"""``WorkItemStore`` — the hub-owned work item repository (issue #357, component tier).

Exercises ``create``/``close``/``get`` through the read/write Protocol split
(``bzh:repository-split``): every write is read back through the read variant alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.work import IReadWorkItemRepository, WorkItemAuthor, WorkItemClosure
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.work_item_store import WorkItemStore

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> WorkItemStore:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return WorkItemStore(create_engine_from_url(db_url))


def test_get_of_an_unknown_ref_is_none(tmp_path: Path) -> None:
    reader: IReadWorkItemRepository = _store(tmp_path)
    assert reader.get("hub", "1") is None


def test_create_reads_back_open_with_no_closure(tmp_path: Path) -> None:
    store = _store(tmp_path)

    created = store.create(
        source="hub",
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

    first = store.create(
        source="hub", title="a", body="a", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
    )
    second = store.create(
        source="hub", title="b", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
    )

    assert first.ref != second.ref
    assert int(second.ref) > int(first.ref)


def test_close_is_unset_until_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.create(
        source="hub", title="a", body="a", author=WorkItemAuthor.fleet(), stated_priority=None, at=_NOW
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
