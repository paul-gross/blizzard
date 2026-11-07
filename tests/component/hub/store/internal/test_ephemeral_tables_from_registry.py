"""The chunk readers and the work-refs signature take their ephemeral exclusions from the fact-family registry
(component tier): a stand-in ephemeral family registered there is honoured with no consumer edit."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import fact_families
from blizzard.hub.store.internal.fact_families import ephemeral_tables
from tests.support.chunk_world import every_branch, hub_with_graph
from tests.support.scaffolding import HubHarness, stamp

_md = MetaData()
_STAND_IN = Table(
    "chunk_stand_in_ephemeral",
    _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
)


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HubHarness:
    hub = hub_with_graph(tmp_path)
    _md.create_all(hub.engine)
    entry = replace(
        fact_families.entry_of(s.chunk_deleted), table=_STAND_IN, chunk_key=_STAND_IN.c.chunk_id, row_id=_STAND_IN.c.id
    )
    monkeypatch.setattr(fact_families, "FACT_TABLES", (*fact_families.FACT_TABLES, entry))
    assert _STAND_IN in ephemeral_tables()
    return hub


def _mark(hub: HubHarness, chunk_id: str) -> None:
    with hub.engine.begin() as conn:
        conn.execute(insert(_STAND_IN).values(stamp(_STAND_IN, chunk_id=chunk_id)))


def test_a_registered_ephemeral_family_is_excluded_by_every_chunk_reader(stand_in: HubHarness) -> None:
    hub = stand_in
    every_branch(hub)
    record = hub.services.chunks.record
    victim = record.newest_done(limit=1)[0]
    pages_before = {c.chunk_id for c in record.list_page(limit=500).chunks}
    assert victim in pages_before

    _mark(hub, victim)

    assert victim not in record.newest_done(limit=500)
    assert victim not in {c.chunk_id for c in record.list_page(limit=500).chunks}
    assert victim not in record.pinned_to_actionless_hub_nodes()


def test_a_registered_ephemeral_family_moves_the_work_refs_signature(stand_in: HubHarness) -> None:
    hub = stand_in
    every_branch(hub)
    work_refs = hub.services.chunks.work_refs
    before = work_refs.live_work_refs_signature()
    assert work_refs.live_work_refs_signature() == before
    _mark(hub, "ch_stopped_new")
    assert work_refs.live_work_refs_signature() != before


def test_each_live_ephemeral_family_moves_the_work_refs_signature(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    every_branch(hub)
    work_refs = hub.services.chunks.work_refs
    names = [t.name for t in ephemeral_tables()]
    assert {"chunk_grouped", "chunk_deleted"} <= set(names)
    assert {name for name, _, _ in work_refs.live_work_refs_signature().marks} >= set(names)
