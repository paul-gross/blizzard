"""``RunContextStore`` — the run-context repository (blizzard#393 Phase 1, component
tier). Migrated-to-head sqlite-on-disk — the ``tests/test_garden_proposal_store.py``
shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.domain.work import Chunk, WorkItemAuthor, WorkRef
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import hub_store_connections, seed_graph, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_items(tmp_path: Path) -> tuple[RunContextStore, WorkItemStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO scopes (slug, description, created_at) VALUES ('blizzard', '', :now)"),
            {"now": _NOW},
        )
        seed_graph(conn, "gr_1", at=_NOW)
    store_connections = hub_store_connections(engine)
    return RunContextStore(store_connections), WorkItemStore(store_connections), engine


def test_record_then_for_chunk_round_trips(tmp_path: Path) -> None:
    runs, items, _ = _store_and_items(tmp_path)
    item = seed_work_item(items, graph_id="gr_1", author=WorkItemAuthor.user("u_1"), at=_NOW)
    context = RunContext(routine_name="nightly", scope_slug="blizzard", mode="dry_run")

    runs.record(item.work_item_id, context)

    chunk = Chunk(
        chunk_id="ch_probe", graph_id="gr_1", work_refs=[WorkRef(source=item.source, ref=item.ref)], minted_at=_NOW
    )
    assert runs.for_chunk(chunk) == context


def test_for_chunk_resolves_through_the_chunks_first_work_ref(tmp_path: Path) -> None:
    """A chunk naming more than one work ref still resolves off ``work_refs[0]``."""
    runs, items, _ = _store_and_items(tmp_path)
    first = seed_work_item(items, graph_id="gr_1", author=WorkItemAuthor.user("u_1"), at=_NOW)
    second = seed_work_item(items, graph_id="gr_1", author=WorkItemAuthor.user("u_1"), at=_NOW)
    context = RunContext(routine_name="nightly", scope_slug="blizzard", mode="dry_run")
    runs.record(first.work_item_id, context)

    chunk = Chunk(
        chunk_id="ch_probe",
        graph_id="gr_1",
        work_refs=[WorkRef(source=first.source, ref=first.ref), WorkRef(source=second.source, ref=second.ref)],
        minted_at=_NOW,
    )

    assert runs.for_chunk(chunk) == context


def test_for_chunk_with_no_work_item_runs_row_is_none(tmp_path: Path) -> None:
    runs, items, _ = _store_and_items(tmp_path)
    item = seed_work_item(items, graph_id="gr_1", author=WorkItemAuthor.user("u_1"), at=_NOW)

    chunk = Chunk(
        chunk_id="ch_probe", graph_id="gr_1", work_refs=[WorkRef(source=item.source, ref=item.ref)], minted_at=_NOW
    )

    assert runs.for_chunk(chunk) is None


def test_for_chunk_with_no_work_refs_is_none(tmp_path: Path) -> None:
    runs, _, _ = _store_and_items(tmp_path)

    chunk = Chunk(chunk_id="ch_probe", graph_id="gr_1", work_refs=[], minted_at=_NOW)

    assert runs.for_chunk(chunk) is None
