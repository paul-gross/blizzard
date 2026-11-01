"""Runner-store decisions that had only a comment defending them.

Each test below pins one store-level decision whose reversion no other test caught."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store.errors import RunnerStoreError, RunnerStoreErrorFactory
from blizzard.runner.store.schema import (
    OUTBOX_LANE_OUTBOUND,
    OUTBOX_LANE_TRANSCRIPT,
    binding_releases,
    env_bindings,
    leases,
    outbox_counters,
    park_facts,
    pause_park_resumes,
    pause_parks,
    requeues,
)
from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION
from tests.config_inputs import init_host
from tests.runner_fakes import FIXTURE_RUNNER_ID, SqlAlchemyRunnerStore, make_store, runner_store_errors

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path):
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


class _FakeRunnerStoreErrors(RunnerStoreErrorFactory):
    """A fake collaborator that records every wrap without ever touching the real
    logger its base class carries — proves the store's driver-error wrap
    is substitutable by injection."""

    def __init__(self) -> None:
        self.wrapped: list[str] = []

    def from_driver(self, exc: Exception, *, operation: str) -> RunnerStoreError:
        self.wrapped.append(operation)
        return RunnerStoreError(f"fake wrap: {operation}")


def _counter(engine: sa.Engine, lane: str) -> int:
    counters = outbox_counters
    with engine.connect() as conn:
        return conn.execute(
            sa.select(counters.c.last_seq)
            .where(counters.c.runner_id == FIXTURE_RUNNER_ID, counters.c.lane == lane)
            .execution_options(**{RUNNER_UNSCOPED_OPTION: True})
        ).scalar_one()


def _seqs(store: SqlAlchemyRunnerStore, table: sa.Table) -> list[int]:
    """The ``seq`` of each of ``table``'s rows, in the order its rows were appended."""
    with store.engine.connect() as conn:
        return list(
            conn.execute(
                sa.select(table.c.seq).where(table.c.runner_id == FIXTURE_RUNNER_ID).order_by(table.c.seq)
            ).scalars()
        )


def _mint(store: SqlAlchemyRunnerStore, lease_id: str, *, epoch: int = 1, at: datetime = _NOW) -> None:
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            retries_max=2,
            created_at=at,
        )
    )


def _delta(store: SqlAlchemyRunnerStore, segment_id: str, *, cursor: str) -> int:
    (seq,) = store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=cursor,
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    return seq


@pytest.mark.unit
def test_the_driver_error_wrap_is_substitutable_by_injection(tmp_path: Path) -> None:
    """``_wrap`` is an injected ``RunnerStoreErrorFactory``, not a module-level logger
    — a fake factory observes the wrap without reaching into the module."""
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")  # unmigrated: every query is a driver fault
    errors = _FakeRunnerStoreErrors()
    store = SqlAlchemyRunnerStore(engine, errors)

    with pytest.raises(RunnerStoreError):
        store.pending_outbound()

    assert errors.wrapped == ["query"]


@pytest.mark.unit
def test_a_rebind_after_a_release_reads_as_held(tmp_path: Path):
    """Bind -> release -> bind again on the same ``(chunk, env)``: the second binding
    is held, not read as still-released by a naive set-difference predicate."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW + timedelta(minutes=1))
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW + timedelta(minutes=2))

    assert store.held_environment_ids() == ["e1"]
    assert store.live_tenure_chunk_ids() == ["ch_1"]
    assert [b.environment_id for b in store.bindings_for_chunk("ch_1")] == ["e1"]


@pytest.mark.unit
def test_a_same_instant_rebind_after_a_release_reads_as_held(tmp_path: Path):
    """Bind -> release -> bind at one instant: the release closes only the bind appended before it, so the rebind is
    held — a comparison of instants would have the release close both."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW)
    assert store.held_environment_ids() == []
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    assert store.held_environment_ids() == ["e1"]
    assert store.live_tenure_chunk_ids() == ["ch_1"]
    with store.engine.connect() as conn:
        bound = (
            conn.execute(
                sa.select(env_bindings.c.seq)
                .where(env_bindings.c.runner_id == FIXTURE_RUNNER_ID)
                .order_by(env_bindings.c.id)
            )
            .scalars()
            .all()
        )
        (released,) = (
            conn.execute(sa.select(binding_releases.c.seq).where(binding_releases.c.runner_id == FIXTURE_RUNNER_ID))
            .scalars()
            .all()
        )
    assert bound[0] < released < bound[1]


@pytest.mark.unit
def test_a_same_instant_pause_resume_pause_leaves_the_second_park_open(tmp_path: Path):
    """Pause -> resume -> pause under one lease at one instant: the resume closes only the park appended before it."""
    store = _store(tmp_path)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)
    store.record_pause_park_resume(lease_id="lease_1", resumed_at=_NOW)
    assert store.pause_parked_lease_ids() == set()
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)

    assert store.pause_parked_lease_ids() == {"lease_1"}
    with store.engine.connect() as conn:
        parked = (
            conn.execute(
                sa.select(pause_parks.c.seq)
                .where(pause_parks.c.runner_id == FIXTURE_RUNNER_ID)
                .order_by(pause_parks.c.id)
            )
            .scalars()
            .all()
        )
        (resumed,) = (
            conn.execute(sa.select(pause_park_resumes.c.seq).where(pause_park_resumes.c.runner_id == FIXTURE_RUNNER_ID))
            .scalars()
            .all()
        )
    assert parked[0] < resumed < parked[1]


@pytest.mark.unit
def test_the_newest_binding_of_a_chunk_reads_through_its_release(tmp_path: Path):
    """A lease's workspace is read from its newest binding row, released or not, and never from another chunk's."""
    store = _store(tmp_path)
    assert store.newest_binding_for_chunk("ch_1") is None
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/a/e1", bound_at=_NOW, workspace_id="ws_a")
    store.record_binding(
        chunk_id="ch_1", environment_id="e1", workdir="/b/e1", bound_at=_NOW + timedelta(minutes=1), workspace_id="ws_b"
    )
    store.record_binding(chunk_id="ch_2", environment_id="e2", workdir="/a/e2", bound_at=_NOW, workspace_id="ws_a")
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW + timedelta(minutes=2))

    newest = store.newest_binding_for_chunk("ch_1")
    assert store.bindings_for_chunk("ch_1") == []
    assert newest is not None
    assert (newest.workspace_id, newest.workdir) == ("ws_b", "/b/e1")


@pytest.mark.unit
def test_the_newest_binding_of_a_chunk_is_the_latest_recorded_even_at_an_equal_instant(tmp_path: Path):
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/a/e1", bound_at=_NOW, workspace_id="ws_a")
    store.record_binding(chunk_id="ch_1", environment_id="e2", workdir="/b/e2", bound_at=_NOW)

    newest = store.newest_binding_for_chunk("ch_1")
    assert newest is not None
    assert (newest.environment_id, newest.workspace_id) == ("e2", None)


@pytest.mark.unit
def test_release_epoch_floor_and_tenure_read_are_chunk_scoped_at_equal_instants(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for chunk, epoch in (("ch_1", 1), ("ch_2", 9), ("ch_3", 1)):
        store.record_lease(
            NewLease(
                lease_id=f"lease_{chunk}_old",
                chunk_id=chunk,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=epoch,
                retries_max=2,
                created_at=_NOW,
            )
        )
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW)
    with store.engine.connect() as conn:
        floor = conn.execute(
            sa.select(binding_releases.c.lease_epoch_floor).where(binding_releases.c.runner_id == FIXTURE_RUNNER_ID)
        ).scalar_one()
    assert floor == 1
    assert not store.has_lease_in_binding_tenure("ch_1", _NOW)
    assert store.has_lease_in_binding_tenure("ch_2", _NOW)
    assert store.has_lease_in_binding_tenure("ch_3", _NOW)
    assert not store.has_lease_in_binding_tenure("ch_2", _NOW + timedelta(seconds=1))
    assert not store.has_lease_in_binding_tenure("ch_missing", _NOW)

    store.record_lease(
        NewLease(
            lease_id="lease_ch_1_new",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_retro",
            node_name="retro",
            epoch=2,
            retries_max=2,
            created_at=_NOW,
        )
    )
    assert store.has_lease_in_binding_tenure("ch_1", _NOW)
    assert not store.has_lease_in_binding_tenure("ch_1", _NOW + timedelta(seconds=1))


@pytest.mark.unit
def test_a_same_instant_escalation_closure_does_not_mask_its_escalation(tmp_path: Path):
    """``>``, not ``>=``: a mark stamped at the escalation's own instant must not close it
    — the sweep always stamps a later tick, and a tie would mask a fresh escalation."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    store.record_escalation_closure(chunk_id="ch_1", reason="stopped", at=_NOW)  # the same instant

    assert [e.lease_id for e in store.open_escalations()] == ["lease_1"]

    store.record_escalation_closure(chunk_id="ch_1", reason="stopped", at=_NOW + timedelta(seconds=1))

    assert store.open_escalations() == []


@pytest.mark.unit
def test_a_same_instant_mint_consumes_its_requeue_mark(tmp_path: Path):
    """A spawn that stamps its lease at the requeue mark's own instant still consumes it, as it is appended after
    the mark — a comparison of instants that left it pending would never see the reattempt happen."""
    store = _store(tmp_path)
    store.record_requeue(chunk_id="ch_1", at=_NOW)
    assert store.pending_requeue_chunk_ids() == {"ch_1"}

    _mint(store, "lease_1")  # the same instant the mark carries

    assert store.pending_requeue_chunk_ids() == set()
    assert _seqs(store, requeues) < _seqs(store, leases)


@pytest.mark.unit
def test_a_same_instant_mint_appended_before_a_requeue_mark_leaves_it_pending(tmp_path: Path):
    """A mark appended after a mint is not that mint's to consume, whatever instant both carry: the reattempt it asks
    for is still to come."""
    store = _store(tmp_path)
    _mint(store, "lease_1")
    store.record_requeue(chunk_id="ch_1", at=_NOW)

    assert store.pending_requeue_chunk_ids() == {"ch_1"}
    assert _seqs(store, leases) < _seqs(store, requeues)

    _mint(store, "lease_2", epoch=2)

    assert store.pending_requeue_chunk_ids() == set()


@pytest.mark.unit
def test_fact_sequences_are_one_runner_wide_across_the_tables(tmp_path: Path):
    """One counter numbers every sequenced table's rows, so rows of different tables are ordered by it."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    _mint(store, "lease_1")
    store.record_requeue(chunk_id="ch_1", at=_NOW)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)

    assert [
        _seqs(store, env_bindings),
        _seqs(store, leases),
        _seqs(store, requeues),
        _seqs(store, pause_parks),
    ] == [[1], [2], [3], [4]]


@pytest.mark.unit
def test_pause_parks_are_their_own_table_and_park_facts_keeps_a_non_null_question_id() -> None:
    """The pause-park split, structurally: ``park_facts.question_id`` stays NOT NULL
    and ``pause_parks`` carries no ``question_id`` at all — one NULL in the
    ``unforwarded_ask`` subquery would break ask-and-exit fleet-wide."""
    assert park_facts.c.question_id.nullable is False
    assert "question_id" not in pause_parks.c


@pytest.mark.unit
def test_set_route_token_keeps_one_current_row_per_chunk(tmp_path: Path):
    """A re-claim of the same chunk overwrites the stashed plaintext rather than appending:
    the runner only ever presents its *current* token, so there is exactly one row to read
    and no rotation history to disambiguate."""
    store = _store(tmp_path)
    store.set_route_token("ch_1", token="rtok-old", at=_NOW)
    store.set_route_token("ch_1", token="rtok-new", at=_NOW + timedelta(minutes=1))

    assert store.route_token("ch_1") == "rtok-new"


@pytest.mark.component
def test_a_migrated_transcript_outbound_seq_is_never_reissued_after_a_prune(tmp_path: Path) -> None:
    """The lane's ``seq`` comes from its durable counter, not the table: pruning the table's only, and highest, row
    leaves the counter on it, so the next delta takes the next ``seq``. Production migrates
    (`bzh:gating-tier-pins-production-paths`), so the pin runs on a migrated store."""
    config = init_host(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        store.record_lease(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=_NOW,
            )
        )
        store.record_spawn(
            "lease_1",
            pid=1,
            process_start_time="1",
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
            spawned_at=_NOW,
        )
        segment_id = store.open_transcript_segments()[0].segment_id
        first = _delta(store, segment_id, cursor="tok-1")
        store.ack_transcript_outbound(first, acked_at=_NOW)  # prunes the table's only, and highest, row
        assert _counter(engine, OUTBOX_LANE_TRANSCRIPT) == first
        second = _delta(store, segment_id, cursor="tok-2")
    finally:
        engine.dispose()

    assert second == first + 1


@pytest.mark.component
def test_a_migrated_outbound_seq_is_never_reissued_after_a_prune(tmp_path: Path) -> None:
    """``outbound_buffer`` counts the same way, and retention prunes it too: with every issued ``seq`` acked and pruned,
    the counter still holds the highest, and the next fact takes the one after it. Production migrates
    (`bzh:gating-tier-pins-production-paths`), so the pin runs on a migrated store."""
    config = init_host(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        old = _NOW - timedelta(days=8)  # past the 7-day retention window
        seqs = [
            store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=old)
            for _ in range(3)
        ]
        for seq in seqs:
            store.ack_outbound(seq, acked_at=old)  # every issued seq acked — none pending, no floor to respect

        pruned = store.prune_outbound(now=_NOW)
        assert pruned == len(seqs)  # every acked row, including the table's own highest seq, is gone
        assert _counter(engine, OUTBOX_LANE_OUTBOUND) == max(seqs)

        fresh = store.enqueue_outbound(
            kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=_NOW
        )
    finally:
        engine.dispose()

    assert seqs == [1, 2, 3]
    assert fresh == max(seqs) + 1


@pytest.mark.component
def test_each_runner_counts_its_own_outbound_seq_in_one_store(tmp_path: Path) -> None:
    """Two runners over one file each take ``seq`` 1, 2, ... from a counter of their own."""
    config = init_host(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        first = SqlAlchemyRunnerStore(engine, runner_store_errors(), "rn_first")
        second = SqlAlchemyRunnerStore(engine, runner_store_errors(), "rn_second")
        enqueue = {"kind": "lease.minted", "chunk_id": None, "lease_id": None, "payload": "{}", "created_at": _NOW}
        seqs = (
            [first.enqueue_outbound(**enqueue) for _ in range(2)],
            [second.enqueue_outbound(**enqueue) for _ in range(3)],
            [first.enqueue_outbound(**enqueue)],
        )
        pending = ([f.seq for f in first.pending_outbound()], [f.seq for f in second.pending_outbound()])
    finally:
        engine.dispose()

    assert seqs == ([1, 2], [1, 2, 3], [3])
    assert pending == ([1, 2, 3], [1, 2, 3])
