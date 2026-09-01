"""``IReadChunkFactsRepository.load_all_facts`` — the fleet-summary bulk read (component
tier, issue #374).

Proves the bulk read derives the exact same status per chunk as ``load_facts`` called
one chunk at a time, across a fixture spanning every derived status plus the edge
shapes AC4 names (a chunks-only chunk, a grouped chunk, a deleted chunk, and a chunk
whose movement facts span two graphs) — and that it does so in a bounded number of
queries, evaluating ``ephemeral_ids`` at most once, regardless of fleet size."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, insert

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.hub.api.chunks import FleetPulse
from blizzard.hub.domain.chunks.stores import ChunkStores
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.work import Chunk, ChunkFacts, DecisionChoice, FleetSummary, MigrationSource
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_facts_store as chunk_facts_store_module
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.chunk_rows import record_deleted_row
from tests.support import build_hub, chunk_stores, count_queries, hub_store_connections, ingest, migrate_to, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _seed_node(conn: sa.Connection, graph_id: str, node_id: str, *, executor: str) -> None:
    conn.execute(
        insert(s.graph_nodes).values(
            node_id=node_id, graph_id=graph_id, name=node_id, executor=executor, session="resume", judged_by="worker"
        )
    )


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_graph(conn, "gr_2", at=_T0)
        _seed_node(conn, "gr_1", "nd_g1_runner", executor="runner")
        _seed_node(conn, "gr_1", "nd_g1_hub", executor="hub")
        _seed_node(conn, "gr_2", "nd_g2_runner", executor="runner")
        _seed_node(conn, "gr_2", "nd_g2_hub", executor="hub")
    return chunk_stores(engine, FixedClock(_T0)), engine


def _mint(store: ChunkStores, chunk_id: str, *, graph_id: str = "gr_1") -> None:
    store.record.mint(Chunk(chunk_id=chunk_id, graph_id=graph_id, work_refs=[], minted_at=_T0))


def _seed_fixture(store: ChunkStores, engine: Engine) -> None:
    """One chunk per derived status, plus the AC4 edge shapes and a kitchen-sink chunk
    touching every other fact family ``load_facts`` reads."""
    _mint(store, "ch_not_ready")  # a chunks-only row (AC4): derives NOT_READY

    _mint(store, "ch_ready")
    store.queue.record_promote("ch_ready", at=_T0)

    _mint(store, "ch_running")
    store.queue.record_promote("ch_running", at=_T0)
    store.route.record_route(
        Route(chunk_id="ch_running", runner_id="r1", workspace_id="w1", environment_ids=["e1"], created_at=_T0),
        token_hash="th_running",
        at=_T0,
    )

    _mint(store, "ch_delivering")
    store.queue.record_promote("ch_delivering", at=_T0)
    store.movement.record_transition(
        transition_id="tr_delivering",
        chunk_id="ch_delivering",
        from_node_id=None,
        to_node_id="nd_g1_hub",
        choice_name=None,
        epoch=1,
        runner_id="r",
        at=_T0,
        artifacts=[],
        proposals=[],
    )

    _mint(store, "ch_waiting")
    store.queue.record_promote("ch_waiting", at=_T0)
    store.questions.record_question(
        question_id="qn_1",
        chunk_id="ch_waiting",
        node_id=None,
        session_id=None,
        runner_id="r",
        epoch=1,
        question="which branch?",
        options=[],
        asked_at=_T0,
    )

    _mint(store, "ch_needs")
    store.queue.record_promote("ch_needs", at=_T0)
    store.escalations.record_escalation("ch_needs", epoch=1, takeover_command="cmd", at=_T0)

    _mint(store, "ch_paused")
    store.queue.record_promote("ch_paused", at=_T0)
    # Newest-fact-wins (issue #46): paused, then resumed, then paused again — the tail
    # (paused=True) must win, proving the explicit pause order_by (D4) round-trips.
    store.lifecycle.record_pause("ch_paused", paused=True, by="op", at=_at(0))
    store.lifecycle.record_pause("ch_paused", paused=False, by="op", at=_at(1))
    store.lifecycle.record_pause("ch_paused", paused=True, by="op", at=_at(2))

    _mint(store, "ch_stopped")
    store.queue.record_promote("ch_stopped", at=_T0)
    store.route.record_route(
        Route(chunk_id="ch_stopped", runner_id="r1", workspace_id="w1", environment_ids=["e1"], created_at=_T0),
        token_hash="th_stopped",
        at=_T0,
    )
    store.lifecycle.record_stop("ch_stopped", by="op", at=_at(1))

    _mint(store, "ch_done_completed")
    store.queue.record_promote("ch_done_completed", at=_T0)
    store.lifecycle.record_completion("ch_done_completed", by="op", at=_T0)

    _mint(store, "ch_done_terminal")
    store.queue.record_promote("ch_done_terminal", at=_T0)
    store.movement.record_transition(
        transition_id="tr_terminal",
        chunk_id="ch_done_terminal",
        from_node_id="nd_g1_runner",
        to_node_id=RESERVED_TERMINAL,
        choice_name="go",
        epoch=1,
        runner_id="r",
        at=_T0,
        artifacts=[],
        proposals=[],
    )

    # AC4: movement facts spanning two graphs — a transition on gr_1, then a migration
    # landing on gr_2's hub node. The executor for that landing must resolve against
    # gr_2, not the chunk's gr_1 mint pin (issues #90, #111).
    _mint(store, "ch_multigraph", graph_id="gr_1")
    store.queue.record_promote("ch_multigraph", at=_T0)
    store.movement.record_transition(
        transition_id="tr_multigraph",
        chunk_id="ch_multigraph",
        from_node_id=None,
        to_node_id="nd_g1_runner",
        choice_name=None,
        epoch=1,
        runner_id="r",
        at=_T0,
        artifacts=[],
        proposals=[],
    )
    store.movement.record_migration(
        "ch_multigraph",
        from_node_id="nd_g1_runner",
        from_graph_id="gr_1",
        to_graph_id="gr_2",
        landed_node_id="nd_g2_hub",
        choice_name="go",
        model=None,
        epoch=2,
        at=_at(1),
        artifacts=[],
        proposals=[],
        source=MigrationSource.AUTHORED_EDGE,
    )

    # AC4: grouped-away and deleted chunks are ephemeral — excluded from every read.
    _mint(store, "ch_grouped")
    store.queue.record_promote("ch_grouped", at=_T0)
    store.lifecycle.record_grouped("ch_grouped", grouped_into="ch_ready", at=_T0)

    _mint(store, "ch_deleted")
    with engine.begin() as conn:
        record_deleted_row(conn, "ch_deleted", by="op", at=_T0)

    # A kitchen-sink chunk exercising every remaining fact family ``load_facts`` reads,
    # so the bulk read's completeness (D2) is proven, not merely asserted.
    _mint(store, "ch_kitchen_sink")
    store.queue.record_promote("ch_kitchen_sink", at=_T0)
    store.route.record_lease("ch_kitchen_sink", epoch=1, runner_id="r", at=_T0)
    store.route.record_route(
        Route(chunk_id="ch_kitchen_sink", runner_id="r1", workspace_id="w1", environment_ids=["e1"], created_at=_T0),
        token_hash="th_ks_1",
        at=_T0,
    )
    store.route.record_route_token("ch_kitchen_sink", token_hash="th_ks_2", at=_at(1))
    store.route.record_route_released("ch_kitchen_sink", at=_at(2))
    store.movement.record_requeue("ch_kitchen_sink", at=_at(2))
    store.usage.record_usage(
        "ch_kitchen_sink",
        node_id="nd_g1_runner",
        epoch=1,
        runner_id="r",
        kind="agent_turn",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.1,
        at=_T0,
    )
    store.escalations.record_bounce("ch_kitchen_sink", epoch=1, cause="conflict", envelope="{}", at=_T0)
    store.hub_exec.record_hub_node_poll("ch_kitchen_sink", node_id="nd_g1_hub", epoch=1, at=_T0)
    store.delivery.record_delivery_repo_landed("ch_kitchen_sink", repo="r1", commit_hash="c1", at=_T0)
    store.delivery.record_delivery_landed("ch_kitchen_sink", at=_T0)
    store.decisions.record_decision(
        decision_id="dec_1",
        chunk_id="ch_kitchen_sink",
        node_id="nd_g1_hub",
        node_name="n",
        epoch=1,
        choices=[DecisionChoice(name="ok", description="d")],
        at=_T0,
        artifacts=[],
        proposals=[],
    )
    store.decisions.record_decision_resolution("dec_1", choice="ok", resolved_by="op", at=_at(1))
    store.movement.record_restart(
        "ch_kitchen_sink", from_node_id="nd_g1_runner", to_node_id="nd_g1_runner", by="op", at=_at(3)
    )
    with engine.begin() as conn:
        conn.execute(
            insert(s.delivery_pr_opened).values(
                chunk_id="ch_kitchen_sink", repo="r1", pr_number=1, pr_url="http://x/1", commit_hash="c1", opened_at=_T0
            )
        )
        conn.execute(
            insert(s.delivery_pr_closed).values(
                chunk_id="ch_kitchen_sink", repo="r1", pr_number=1, merged=True, landed_commit="c1", closed_at=_at(1)
            )
        )


_LIVE_CHUNK_IDS = [
    "ch_not_ready",
    "ch_ready",
    "ch_running",
    "ch_delivering",
    "ch_waiting",
    "ch_needs",
    "ch_paused",
    "ch_stopped",
    "ch_done_completed",
    "ch_done_terminal",
    "ch_multigraph",
    "ch_kitchen_sink",
]


def test_bulk_read_status_matches_per_chunk_load_facts_across_every_derived_status(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_fixture(store, engine)

    bulk = store.facts.load_all_facts()

    assert set(bulk) == set(_LIVE_CHUNK_IDS)  # grouped/deleted excluded (AC4)
    statuses_seen = set()
    for chunk_id in _LIVE_CHUNK_IDS:
        expected = store.facts.load_facts(chunk_id)
        assert expected is not None
        assert bulk[chunk_id].status() == expected.status(), chunk_id
        statuses_seen.add(expected.status())
    assert statuses_seen == set(ChunkStatus)  # the fixture spans every derived status


def test_bulk_read_bucket_counts_match_the_per_chunk_fold(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_fixture(store, engine)

    via_bulk = FleetSummary.of(facts.status() for facts in store.facts.load_all_facts().values())
    # The pre-#374 shape ``FleetPulse.view()`` used, called out here as the equivalence
    # baseline (D6) rather than imported from ``chunks.py``, since that call site is gone.
    via_per_chunk = FleetSummary.of(
        (store.facts.load_facts(c.chunk_id) or ChunkFacts(minted=True)).status() for c in store.record.list_all()
    )

    assert via_bulk == via_per_chunk
    # ready: ch_ready, ch_multigraph is delivering not ready — sanity a couple of buckets.
    assert via_bulk.ready >= 1
    assert via_bulk.running >= 1  # ch_running + ch_delivering + ch_multigraph
    assert via_bulk.waiting >= 1  # ch_waiting + ch_paused
    assert via_bulk.needs == 1  # ch_needs


def test_bulk_read_excludes_the_chunks_only_chunk_from_every_bucket(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _seed_fixture(store, engine)

    summary = FleetSummary.of(facts.status() for facts in store.facts.load_all_facts().values())

    assert store.facts.load_all_facts()["ch_not_ready"].status() is ChunkStatus.NOT_READY
    assert summary.ready + summary.running + summary.waiting + summary.needs < len(_LIVE_CHUNK_IDS)


def test_bulk_read_resolves_a_migrated_landing_node_against_its_own_graph(tmp_path: Path) -> None:
    """The multigraph chunk's landing node (``nd_g2_hub``) lives only in ``gr_2`` — a
    global, ungraphed executor lookup would silently default it to RUNNER."""
    store, engine = _store(tmp_path)
    _seed_fixture(store, engine)

    bulk = store.facts.load_all_facts()

    assert bulk["ch_multigraph"].status() is ChunkStatus.DELIVERING
    migration = bulk["ch_multigraph"].newest_migration()
    assert migration is not None
    from blizzard.foundation.node_steps import Executor

    assert migration.landed_node_executor is Executor.HUB


def test_ephemeral_ids_evaluated_at_most_once_per_bulk_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, engine = _store(tmp_path)
    _seed_fixture(store, engine)

    calls = {"n": 0}
    original = chunk_facts_store_module.ephemeral_ids

    def counting(conn: sa.Connection) -> set[str]:
        calls["n"] += 1
        return original(conn)

    monkeypatch.setattr(chunk_facts_store_module, "ephemeral_ids", counting)

    store.facts.load_all_facts()

    assert calls["n"] == 1


def test_bulk_read_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine = _store(tmp_path / "small")
    small.record.mint(Chunk(chunk_id="ch_a", graph_id="gr_1", work_refs=[], minted_at=_T0))
    small.queue.record_promote("ch_a", at=_T0)

    large, large_engine = _store(tmp_path / "large")
    for i in range(40):
        large.record.mint(Chunk(chunk_id=f"ch_{i}", graph_id="gr_1", work_refs=[], minted_at=_T0))
        large.queue.record_promote(f"ch_{i}", at=_T0)

    small_count = count_queries(small_engine, small.facts.load_all_facts)
    large_count = count_queries(large_engine, large.facts.load_all_facts)

    assert small_count == large_count
    assert large_count < 40  # bounded by table count, not chunk count


def test_fleet_pulse_view_calls_load_all_facts_and_never_load_facts_or_list_all(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    ingest(hub, [{"source": "default", "ref": "2"}])

    class _CountingFactsStore(ChunkFactsStore):
        def __init__(self, store, clock) -> None:  # type: ignore[no-untyped-def]
            super().__init__(store, clock)
            self.load_all_facts_calls = 0
            self.load_facts_calls = 0

        def load_all_facts(self):  # type: ignore[no-untyped-def]
            self.load_all_facts_calls += 1
            return super().load_all_facts()

        def load_facts(self, chunk_id: str):  # type: ignore[no-untyped-def]
            self.load_facts_calls += 1
            return super().load_facts(chunk_id)

    class _CountingRecordStore(ChunkRecordStore):
        def __init__(self, store, clock, *, facts) -> None:  # type: ignore[no-untyped-def]
            super().__init__(store, clock, facts=facts)
            self.list_all_calls = 0

        def list_all(self):  # type: ignore[no-untyped-def]
            self.list_all_calls += 1
            return super().list_all()

    counting_facts = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    counting_record = _CountingRecordStore(hub_store_connections(hub.engine), hub.clock, facts=counting_facts)
    services = replace(hub.services, chunks=replace(hub.services.chunks, facts=counting_facts, record=counting_record))

    view = FleetPulse(services).view()

    assert counting_facts.load_all_facts_calls == 1
    assert counting_facts.load_facts_calls == 0
    assert counting_record.list_all_calls == 0
    assert view.ready == 2
