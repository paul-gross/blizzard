"""The sweep corpus reads (``SweepCorpusReads``) over a real, migrated store holding three tenants —
the carried-over one, a second open one, and a closed one: every corpus read returns the open
tenants' rows only, each tagged with its tenant, and never a row naming no tenant; the probes count
every tenant; and every statement the group issues carries the hub-scoped marker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.observability.egress.repository import EpochKey, EventsPosition, UsagePosition
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.hub_scoped_corpus import SweepCorpusReads
from blizzard.hub.store.internal.hub_scoped_reads import hub_scoped_read
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, TenantId
from tests.support import default_tenant, hub_level_connections, hub_scoped

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 8, tzinfo=UTC)
_BEFORE = _AT - timedelta(seconds=1)
_AFTER = _AT + timedelta(seconds=1)
_EXTRACTOR = "x1"
_OPEN = TenantId("ten_open")
_CLOSED = TenantId("ten_closed")


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
        self.engine = create_engine_from_url(db_url)
        self.carried = default_tenant(self.engine)
        self.corpus = SweepCorpusReads(partial(hub_scoped_read, hub_level_connections(self.engine)))
        for tenant in (_OPEN, _CLOSED):
            self.insert(s.tenants, tenant_id=tenant, name=tenant, created_at=_AT, created_by="test")
        for tenant in (self.carried, _OPEN, _CLOSED):
            self._seed_tenant(tenant)

    def close(self, tenant: TenantId) -> None:
        self.insert(s.tenant_deletions, tenant_id=tenant, name=tenant, deleted_by="test")

    def insert(self, table: sa.Table, **values: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(hub_scoped(sa.insert(table).values(**values)))

    def _seed_tenant(self, tenant: TenantId) -> None:
        """A live chunk and a deleted one, each holding a work ref and a pending close intent, and
        a visible, derived transcript segment of the live chunk — every row in ``tenant``."""
        self.insert(
            s.graphs,
            tenant_id=tenant,
            graph_id=f"gr_{tenant}",
            name="g",
            entry_node_id="nd_1",
            definition_yaml="",
            created_at=_AT,
        )
        for chunk_id in (_live(tenant), _gone(tenant)):
            self.insert(s.chunks, tenant_id=tenant, chunk_id=chunk_id, graph_id=f"gr_{tenant}", minted_at=_AT, model="")
            self.insert(s.chunk_work_refs, tenant_id=tenant, chunk_id=chunk_id, source="src", ref=chunk_id)
            self.insert(
                s.close_intents, tenant_id=tenant, chunk_id=chunk_id, source="src", ref=chunk_id, enqueued_at=_AT
            )
        self.insert(s.chunk_deleted, tenant_id=tenant, chunk_id=_gone(tenant), deleted_at=_AT, deleted_by="test")
        self._seed_export_facts(tenant)
        self.insert(
            s.transcript_segments,
            tenant_id=tenant,
            segment_id=_segment(tenant),
            chunk_id=_live(tenant),
            node_id="nd_1",
            epoch=1,
            spawn_generation=0,
            runner_id="r1",
            turn_range_start=0,
            turn_range_end=1,
            final=True,
            rejected=False,
            byte_count=0,
            normalizer_version="n1",
            received_at=_AT,
            content_digest="d",
        )
        self.insert(
            s.transcript_event_derivations,
            tenant_id=tenant,
            segment_id=_segment(tenant),
            extractor_version=_EXTRACTOR,
            content_fingerprint="stale",
            derived_at=_AT,
            event_count=0,
            complete=True,
        )

    def _seed_export_facts(self, tenant: TenantId) -> None:
        """The live chunk's closing transition, lease, usage and a dropped segment — what the trace and
        egress sweeps read."""
        live = _live(tenant)
        self.insert(
            s.transitions,
            tenant_id=tenant,
            transition_id=f"tr_{tenant}",
            chunk_id=live,
            graph_id=f"gr_{tenant}",
            to_node_id="nd_2",
            epoch=1,
            runner_id="r1",
            recorded_at=_AT,
        )
        self.insert(s.lease_facts, tenant_id=tenant, chunk_id=live, epoch=1, runner_id="r1", minted_at=_AT)
        self.insert(
            s.usage_facts,
            tenant_id=tenant,
            chunk_id=live,
            node_id="nd_1",
            epoch=1,
            runner_id="r1",
            kind="spawn",
            model="m",
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            cache_create_tokens=0,
            recorded_at=_AT,
        )
        self.insert(
            s.transcript_event_drops,
            tenant_id=tenant,
            segment_id=f"sg_dropped_{tenant}",
            chunk_id=live,
            epoch=1,
            spawn_generation=0,
            dropped_at=_AT,
        )


def _live(tenant: str) -> str:
    return f"ch_live_{tenant}"


def _gone(tenant: str) -> str:
    return f"ch_gone_{tenant}"


def _segment(tenant: str) -> str:
    return f"sg_{tenant}"


@pytest.fixture
def world(tmp_path: Path) -> _World:
    built = _World(tmp_path)
    built.close(_CLOSED)
    return built


def test_live_work_refs_are_the_open_tenants_rows_each_with_its_tenant(world: _World) -> None:
    rows = {(row.tenant, row.chunk_id, row.ref) for row in world.corpus.live_work_refs()}
    assert rows == {
        (tenant, _live(tenant), WorkRef(source="src", ref=_live(tenant))) for tenant in (world.carried, _OPEN)
    }


def test_pending_close_intents_are_the_open_tenants_rows_each_with_its_tenant(world: _World) -> None:
    rows = {(row.tenant, row.intent.chunk_id) for row in world.corpus.pending_close_intents()}
    assert rows == {(world.carried, _live(world.carried)), (_OPEN, _live(_OPEN))}


def test_candidacy_is_keyed_by_open_tenant(world: _World) -> None:
    read = world.corpus.candidacy(_EXTRACTOR)
    assert set(read) == {world.carried, _OPEN}
    for tenant in (world.carried, _OPEN):
        assert read[tenant].visible_segment_ids == frozenset({_segment(tenant)})
        assert read[tenant].candidate_segment_ids == [_segment(tenant)]


def test_derived_segment_ids_are_keyed_by_open_tenant(world: _World) -> None:
    assert world.corpus.derived_segment_ids() == {
        world.carried: frozenset({_segment(world.carried)}),
        _OPEN: frozenset({_segment(_OPEN)}),
    }


def test_closing_candidates_are_the_open_tenants_chunks(world: _World) -> None:
    candidates = world.corpus.closing_candidates(_BEFORE, _AFTER, 50)
    assert set(candidates.chunk_ids) == {_live(world.carried), _live(_OPEN)}


def test_step_facts_are_the_open_tenants_chunks_each_tagged_with_its_tenant(world: _World) -> None:
    facts = world.corpus.step_facts_for([_live(t) for t in (world.carried, _OPEN, _CLOSED)], lambda ref: ref.ref)
    assert {chunk_id: f.tenant for chunk_id, f in facts.items()} == {
        _live(world.carried): world.carried,
        _live(_OPEN): _OPEN,
    }
    assert facts[_live(_OPEN)].work_refs == (_live(_OPEN),)


def test_usage_pages_are_the_open_tenants_rows_each_with_its_tenant(world: _World) -> None:
    rows = {(row.tenant, row.chunk_id) for row in world.corpus.usage_after(UsagePosition(_BEFORE), _AFTER, 50)}
    assert rows == {(world.carried, _live(world.carried)), (_OPEN, _live(_OPEN))}


def test_marker_and_drop_pages_are_the_open_tenants_rows_each_with_its_tenant(world: _World) -> None:
    markers = world.corpus.markers_after(EventsPosition(_BEFORE), _AFTER, 50, extractor_version=None)
    drops = world.corpus.drops_after(EventsPosition(_BEFORE), _AFTER, 50)
    assert {(m.tenant, m.segment_id) for m in markers} == {(t, _segment(t)) for t in (world.carried, _OPEN)}
    assert {(d.tenant, d.segment_id) for d in drops} == {(t, f"sg_dropped_{t}") for t in (world.carried, _OPEN)}
    derived = world.corpus.derivations(markers)
    assert {(d.marker.tenant, d.chunk_id) for d in derived} == {(t, _live(t)) for t in (world.carried, _OPEN)}


def test_backfill_epochs_are_the_open_tenants_each_with_its_tenant(world: _World) -> None:
    epochs = world.corpus.epochs_minted_between(_BEFORE, _AFTER, None, 50)
    assert {(e.tenant, e.chunk_id) for e in epochs} == {(t, _live(t)) for t in (world.carried, _OPEN)}
    every = [EpochKey(_live(t), 1) for t in (world.carried, _OPEN, _CLOSED)]
    markers = world.corpus.epoch_markers(every, extractor_version=_EXTRACTOR)
    drops = world.corpus.epoch_drops(every)
    assert {(m.tenant, m.segment_id) for m in markers} == {(t, _segment(t)) for t in (world.carried, _OPEN)}
    assert {(d.tenant, d.segment_id) for d in drops} == {(t, f"sg_dropped_{t}") for t in (world.carried, _OPEN)}


@pytest.mark.parametrize("closed", [False, True], ids=["no-tenant-closed", "a-tenant-closed"])
def test_a_row_naming_no_tenant_is_left_out_whether_or_not_a_tenant_is_closed(tmp_path: Path, closed: bool) -> None:
    world = _World(tmp_path)
    if closed:
        world.close(_CLOSED)
    live = _live(world.carried)
    world.insert(
        s.usage_facts,
        tenant_id=None,
        chunk_id=live,
        node_id="nd_1",
        epoch=1,
        runner_id="r1",
        kind="untenanted",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        recorded_at=_AT,
    )
    assert None not in {row.tenant for row in world.corpus.usage_after(UsagePosition(_BEFORE), _AFTER, 50)}


def test_the_probes_count_every_tenant_and_a_close_moves_the_work_refs_probe(tmp_path: Path) -> None:
    world = _World(tmp_path)
    before = world.corpus.live_work_refs_signature()
    assert world.corpus.derivation_signature().segment_count == 3
    world.close(_CLOSED)
    assert world.corpus.live_work_refs_signature() != before
    assert world.corpus.derivation_signature().segment_count == 3


def test_every_corpus_statement_carries_the_hub_scoped_marker(world: _World) -> None:
    marked: list[bool] = []

    def record(conn: sa.Connection, *_: object) -> None:
        marked.append(bool(conn.get_execution_options().get(HUB_SCOPED_OPTION)))

    event.listen(world.engine, "before_execute", record)
    try:
        world.corpus.live_work_refs()
        world.corpus.live_work_refs_signature()
        world.corpus.pending_close_intents()
        world.corpus.derivation_signature()
        world.corpus.candidacy(_EXTRACTOR)
        world.corpus.derived_segment_ids()
        world.corpus.closing_candidates(_BEFORE, _AFTER, 50)
        world.corpus.step_facts_for([_live(_OPEN)], lambda ref: ref.ref)
        world.corpus.usage_after(UsagePosition(_BEFORE), _AFTER, 50)
        markers = world.corpus.markers_after(EventsPosition(_BEFORE), _AFTER, 50, extractor_version=None)
        world.corpus.drops_after(EventsPosition(_BEFORE), _AFTER, 50)
        world.corpus.derivations(markers)
        epochs = world.corpus.epochs_minted_between(_BEFORE, _AFTER, None, 50)
        world.corpus.epoch_markers(epochs, extractor_version=None)
        world.corpus.epoch_drops(epochs)
    finally:
        event.remove(world.engine, "before_execute", record)
    assert marked
    assert all(marked)
