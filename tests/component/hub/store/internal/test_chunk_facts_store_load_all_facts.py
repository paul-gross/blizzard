"""``IReadChunkFactsRepository.load_all_facts`` — the fleet-summary bulk read (component
tier).

Proves the bulk read derives the exact same status per chunk as ``load_facts`` called
one chunk at a time, across a fixture spanning every derived status plus the edge
shapes AC4 names (a chunks-only chunk, a grouped chunk, a deleted chunk, and a chunk
whose movement facts span two graphs) — and that it does so in a bounded number of
queries, evaluating ``ephemeral_ids`` at most once, regardless of fleet size."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store import batching as batching_module
from blizzard.hub.api.chunks import FleetPulse
from blizzard.hub.domain.chunk.model import (
    BounceFact,
    Chunk,
    ChunkFacts,
    DecisionChoice,
    EpochOwnerFact,
    FleetSummary,
    HubNodePollFact,
    RouteTokenMintedFact,
    UsageFact,
    decide_close_enqueue,
)
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.store.internal import chunk_rows as chunk_rows_module
from blizzard.hub.store.internal.chunk_facts_store import ALL_FAMILIES, STATUS_FAMILIES, ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.scoped import ScopedConnections
from tests.support.load_all_facts_seed import (
    LIVE_CHUNK_IDS,
    T0,
    _mint,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.load_all_facts_seed
    _seed_node,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.load_all_facts_seed
    seed_fixture,
    seeded_chunk_stores,
)
from tests.support.scaffolding import (
    build_hub,
    capture_statements,
    count_queries,
    hub_store_connections,
    ingest,
    seed_chunk_record,
    seed_graph,
)


def test_bulk_read_status_matches_per_chunk_load_facts_across_every_derived_status(tmp_path: Path) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    bulk = store.facts.load_all_facts()

    assert set(bulk) == set(LIVE_CHUNK_IDS)  # grouped/deleted excluded (AC4)
    statuses_seen: set[ChunkStatus] = set()
    for chunk_id in LIVE_CHUNK_IDS:
        expected = store.facts.load_facts(chunk_id)
        assert expected is not None
        assert bulk[chunk_id].status() == expected.status(), chunk_id
        statuses_seen.add(expected.status())
    assert statuses_seen == set(ChunkStatus)  # the fixture spans every derived status


def test_bulk_read_bucket_counts_match_the_per_chunk_fold(tmp_path: Path) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    via_bulk = FleetSummary.of(facts.status() for facts in store.facts.load_all_facts().values())
    # The pre-#374 shape ``FleetPulse.view()`` used, called out here as the equivalence
    # baseline rather than imported from ``src/blizzard/hub/api/chunks.py``, since that call site is gone.
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
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    summary = FleetSummary.of(facts.status() for facts in store.facts.load_all_facts().values())

    assert store.facts.load_all_facts()["ch_not_ready"].status() is ChunkStatus.NOT_READY
    assert summary.ready + summary.running + summary.waiting + summary.needs < len(LIVE_CHUNK_IDS)


def test_bulk_read_resolves_a_migrated_landing_node_against_its_own_graph(tmp_path: Path) -> None:
    """The multigraph chunk's landing node (``nd_g2_hub``) lives only in ``gr_2`` — a
    global, ungraphed executor lookup would silently default it to RUNNER."""
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    bulk = store.facts.load_all_facts()

    assert bulk["ch_multigraph"].status() is ChunkStatus.DELIVERING
    migration = bulk["ch_multigraph"].newest_migration()
    assert migration is not None
    from blizzard.foundation.node_steps import Executor

    assert migration.landed_node_executor is Executor.HUB


def test_ephemeral_ids_evaluated_at_most_once_per_bulk_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    calls = {"n": 0}
    original = chunk_rows_module.ephemeral_ids

    def counting(conn: sa.Connection) -> set[str]:
        calls["n"] += 1
        return original(conn)

    # `load_all_facts`'s exclusion runs through `chunk_rows.graph_id_of_batch` now,
    # so this patches `chunk_rows`, not this module's own former staticmethod.
    monkeypatch.setattr(chunk_rows_module, "ephemeral_ids", counting)

    store.facts.load_all_facts()

    assert calls["n"] == 1


def test_bulk_read_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine, _small_clock = seeded_chunk_stores(tmp_path / "small")
    seed_chunk_record(small, Chunk(chunk_id="ch_a", graph_id="gr_1", work_refs=[], minted_at=T0))
    small.queue.record_promote("ch_a", at=T0)

    large, large_engine, _large_clock = seeded_chunk_stores(tmp_path / "large")
    for i in range(40):
        seed_chunk_record(large, Chunk(chunk_id=f"ch_{i}", graph_id="gr_1", work_refs=[], minted_at=T0))
        large.queue.record_promote(f"ch_{i}", at=T0)

    small_count = count_queries(small_engine, small.facts.load_all_facts)
    large_count = count_queries(large_engine, large.facts.load_all_facts)

    assert small_count == large_count
    assert large_count < 40  # bounded by table count, not chunk count


def test_fleet_pulse_view_calls_load_live_statuses_and_never_load_facts_or_list_all(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ingest(hub, [{"source": "default", "ref": "1"}])
    ingest(hub, [{"source": "default", "ref": "2"}])

    class _CountingFactsStore(ChunkFactsStore):
        def __init__(self, store: ScopedConnections, clock: FixedClock) -> None:
            super().__init__(store, clock)
            self.load_live_statuses_calls = 0
            self.load_facts_calls = 0

        def load_live_statuses(self, *, transaction: Transaction | None = None):
            self.load_live_statuses_calls += 1
            return super().load_live_statuses(transaction=transaction)

        def load_facts(self, chunk_id: str, *, transaction: Transaction | None = None):
            self.load_facts_calls += 1
            return super().load_facts(chunk_id, transaction=transaction)

    class _CountingRecordStore(ChunkRecordStore):
        def __init__(self, store: ScopedConnections, clock: FixedClock) -> None:
            super().__init__(store, clock)
            self.list_all_calls = 0

        def list_all(self):
            self.list_all_calls += 1
            return super().list_all()

    counting_facts = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    counting_record = _CountingRecordStore(hub_store_connections(hub.engine), hub.clock)
    services = replace(hub.services, chunks=replace(hub.services.chunks, facts=counting_facts, record=counting_record))

    view = FleetPulse(services).view()

    assert counting_facts.load_live_statuses_calls == 1
    assert counting_facts.load_facts_calls == 0
    assert counting_record.list_all_calls == 0
    assert view.ready == 2


def test_load_facts_for_matches_per_id_load_facts(tmp_path: Path) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    result = store.facts.load_facts_for(LIVE_CHUNK_IDS)

    assert set(result) == set(LIVE_CHUNK_IDS)
    for chunk_id in LIVE_CHUNK_IDS:
        assert result[chunk_id] == store.facts.load_facts(chunk_id)


def test_load_facts_for_drops_ephemeral_and_unknown_ids(tmp_path: Path) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    result = store.facts.load_facts_for(["ch_ready", "ch_grouped", "ch_deleted", "ch_never_minted"])

    assert set(result) == {"ch_ready"}


def test_load_facts_for_of_no_ids_is_empty(tmp_path: Path) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    assert store.facts.load_facts_for([]) == {}


def test_load_live_statuses_matches_the_non_terminal_load_all_facts_statuses(tmp_path: Path) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    statuses = store.facts.load_live_statuses()
    bulk = store.facts.load_all_facts()

    expected = {chunk_id: facts.status() for chunk_id, facts in bulk.items() if facts.status() not in TERMINAL_STATUSES}
    assert statuses == expected
    assert set(statuses.values()) == set(ChunkStatus) - TERMINAL_STATUSES


def test_status_is_insensitive_to_every_non_status_family(tmp_path: Path) -> None:
    """Every family `ALL_FAMILIES` carries beyond `STATUS_FAMILIES` is populated with a
    value that would change `.status()` if it were (incorrectly) read; `.status()` must come
    back unchanged, mechanically pinning `STATUS_FAMILIES` against `.status()`'s own reach."""
    non_status = ALL_FAMILIES - STATUS_FAMILIES
    assert non_status == {
        "delivery_landed",
        "landed_repos",
        "epoch_owners",
        "route_tokens_minted",
        "usage",
        "bounces",
        "hub_node_polls",
    }

    baseline = ChunkFacts(minted=True, promoted=True)
    loud = replace(
        baseline,
        delivery_landed=True,
        landed_repos=frozenset({"acme/widget"}),
        epoch_owners=[EpochOwnerFact(epoch=1, runner_id="r1")],
        route_tokens_minted=[RouteTokenMintedFact(token_hash="h", minted_at=T0, seq=1)],
        usage=[
            UsageFact(
                node_id="nd_1",
                epoch=1,
                kind="build",
                model="m",
                input_tokens=1,
                output_tokens=1,
                cache_read_tokens=0,
                cache_create_tokens=0,
                cost_usd=None,
                recorded_at=T0,
            )
        ],
        bounces=[BounceFact(epoch=1, cause="c", envelope="{}", recorded_at=T0)],
        hub_node_polls=[HubNodePollFact(node_id="nd_1", epoch=1, polled_at=T0)],
    )

    assert baseline.status() is ChunkStatus.READY
    assert loud.status() == baseline.status()


def test_load_live_statuses_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine, _small_clock = seeded_chunk_stores(tmp_path / "small")
    seed_chunk_record(small, Chunk(chunk_id="ch_a", graph_id="gr_1", work_refs=[], minted_at=T0))
    small.queue.record_promote("ch_a", at=T0)

    large, large_engine, _large_clock = seeded_chunk_stores(tmp_path / "large")
    for i in range(40):
        seed_chunk_record(large, Chunk(chunk_id=f"ch_{i}", graph_id="gr_1", work_refs=[], minted_at=T0))
        large.queue.record_promote(f"ch_{i}", at=T0)

    small_count = count_queries(small_engine, small.facts.load_live_statuses)
    large_count = count_queries(large_engine, large.facts.load_live_statuses)

    assert small_count == large_count
    assert large_count < 40  # bounded by table count, not chunk count


def test_load_live_statuses_reads_fewer_statements_than_load_all_facts(tmp_path: Path) -> None:
    store, engine, clock = seeded_chunk_stores(tmp_path)
    seed_fixture(store, engine, clock)

    statuses_count = count_queries(engine, store.facts.load_live_statuses)
    all_facts_count = count_queries(engine, store.facts.load_all_facts)

    # load_live_statuses skips every family status() never reaches (usage,
    # landed_repos, delivery_landed, route_tokens_minted, hub_node_polls).
    assert statuses_count < all_facts_count


def _seed_promoted_with_open_decision(store: ChunkStores, chunk_id: str) -> None:
    """A promoted chunk carrying one unresolved decision — enough to give every family
    the shared batching helper touches at least one row per seeded id, so a
    per-batch query count stays identical across batches regardless of which id lands
    in which batch."""
    seed_chunk_record(store, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=T0))
    store.queue.record_promote(chunk_id, at=T0)
    store.decisions.record_decision(
        imposed_by_runner_id=None,
        decision_id=f"dec_{chunk_id}",
        chunk_id=chunk_id,
        node_id="nd_g1_hub",
        node_name="n",
        epoch=1,
        choices=[DecisionChoice(name="ok", description="d")],
        at=T0,
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=decide_close_enqueue([], lambda _: True),
    )


def test_load_all_facts_query_count_is_independent_of_decision_count_above_the_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine, _small_clock = seeded_chunk_stores(tmp_path / "small")
    _seed_promoted_with_open_decision(small, "ch_small")
    large, large_engine, _large_clock = seeded_chunk_stores(tmp_path / "large")
    for i in range(10):  # more than three batches of decisions under the lowered cap
        _seed_promoted_with_open_decision(large, f"ch_large_{i}")

    small_count = count_queries(small_engine, small.facts.load_all_facts)
    large_count = count_queries(large_engine, large.facts.load_all_facts)

    assert small_count == large_count


def test_load_facts_for_query_count_scales_with_batch_count_not_id_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, engine, _clock = seeded_chunk_stores(tmp_path)
    ids = [f"ch_batch_{i}" for i in range(7)]  # 3 batches of size 3, 3, 1 under the lowered cap
    for chunk_id in ids:
        _seed_promoted_with_open_decision(store, chunk_id)

    one_batch_count = count_queries(engine, lambda: store.facts.load_facts_for(ids[:3]))
    two_batch_count = count_queries(engine, lambda: store.facts.load_facts_for(ids[:6]))
    three_batch_count = count_queries(engine, lambda: store.facts.load_facts_for(ids))

    per_batch_cost = two_batch_count - one_batch_count
    assert per_batch_cost > 0
    assert two_batch_count == one_batch_count + per_batch_cost
    assert three_batch_count == one_batch_count + 2 * per_batch_cost  # the trailing size-1 batch costs the same

    result = store.facts.load_facts_for(ids)
    assert set(result) == set(ids)
    for chunk_id in ids:
        assert result[chunk_id] == store.facts.load_facts(chunk_id)


def test_load_facts_for_batches_the_graph_node_read_when_chunks_pin_more_graphs_than_a_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 2)
    store, engine, _clock = seeded_chunk_stores(tmp_path)
    ids: list[str] = []
    for i in range(5):  # five distinct pinned graphs under a cap of two
        graph_id = f"gr_extra_{i}"
        with engine.begin() as conn:
            seed_graph(conn, graph_id, at=T0)
            _seed_node(conn, graph_id, f"nd_extra_{i}", executor="hub")
        _mint(store, f"ch_extra_{i}", graph_id=graph_id)
        store.queue.record_promote(f"ch_extra_{i}", at=T0)
        ids.append(f"ch_extra_{i}")

    with capture_statements(engine) as statements:
        loaded = store.facts.load_facts_for(ids)
    node_reads = [p for statement, p in statements if "FROM graph_nodes" in statement]

    assert len(node_reads) == 3  # ceil(5 / 2)
    assert loaded == {chunk_id: store.facts.load_facts(chunk_id) for chunk_id in ids}
    assert store.facts.load_all_facts()["ch_extra_4"] == loaded["ch_extra_4"]
