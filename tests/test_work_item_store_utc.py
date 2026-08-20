"""``WorkItemStore`` round-trips its own written instants (issue #357, ``bzh:utc-instants``).

``created_at``/``edited_at``/``closed_at`` must read back UTC-aware — impossible on a
plain ``DateTime`` column, since sqlite drops ``tzinfo`` on write. See
``tests/test_hub_store_utc.py`` for the sibling proof against ``RunnerRegistryStore``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.work import Chunk, WorkItemAuthor, WorkItemClosure, WorkRef
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import seed_graph

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> WorkItemStore:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
    return WorkItemStore(engine)


def _create(store: WorkItemStore, *, at: datetime):
    """Every hub item's creation mints its resting chunk (blizzard#359) — there is no
    chunkless filing path, so the fixture takes production's own two-step mint."""
    ref = store.allocate_ref("hub")
    pointer = WorkRef(source="hub", ref=ref)
    chunk = Chunk(chunk_id=f"ch_{ref}", graph_id="gr_1", work_refs=[pointer], minted_at=at)
    return store.create_with_chunk(
        pointer=pointer, title="t", body="b", author=WorkItemAuthor.fleet(), stated_priority=None, at=at, chunk=chunk
    )


def test_create_round_trips_its_own_written_instant(tmp_path: Path) -> None:
    store = _store(tmp_path)

    item = _create(store, at=_NOW)

    fetched = store.get("hub", item.ref)
    assert fetched is not None
    assert fetched.created_at == _NOW
    assert fetched.edited_at == _NOW
    assert fetched.created_at.tzinfo is not None
    assert fetched.edited_at.tzinfo is not None


def test_close_round_trips_a_later_instant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    item = _create(store, at=_NOW)
    later = datetime(2026, 7, 16, 12, 5, 0, tzinfo=UTC)

    store.close("hub", item.ref, closure=WorkItemClosure.DELIVERED, at=later)

    fetched = store.get("hub", item.ref)
    assert fetched is not None
    assert fetched.closed_at == later
    assert fetched.closed_at is not None
    assert fetched.closed_at.tzinfo is not None
