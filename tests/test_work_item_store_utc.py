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
from blizzard.hub.domain.work import WorkItemAuthor, WorkItemClosure
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import hub_store_connections, seed_graph, seed_work_item

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> WorkItemStore:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
    return WorkItemStore(hub_store_connections(engine))


def test_create_round_trips_its_own_written_instant(tmp_path: Path) -> None:
    store = _store(tmp_path)

    item = seed_work_item(
        store,
        graph_id="gr_1",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_NOW,
    )

    fetched = store.get("hub", item.ref)
    assert fetched is not None
    assert fetched.created_at == _NOW
    assert fetched.edited_at == _NOW
    assert fetched.created_at.tzinfo is not None
    assert fetched.edited_at.tzinfo is not None


def test_close_round_trips_a_later_instant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    item = seed_work_item(
        store,
        graph_id="gr_1",
        author=WorkItemAuthor.fleet(runner_id="runner-local", chunk_id="ch_seed", node_name="triage"),
        at=_NOW,
    )
    later = datetime(2026, 7, 16, 12, 5, 0, tzinfo=UTC)

    store.close("hub", item.ref, closure=WorkItemClosure.DELIVERED, at=later)

    fetched = store.get("hub", item.ref)
    assert fetched is not None
    assert fetched.closed_at == later
    assert fetched.closed_at is not None
    assert fetched.closed_at.tzinfo is not None
