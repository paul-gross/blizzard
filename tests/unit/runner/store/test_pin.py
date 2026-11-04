"""Runner-store decisions that had only a comment defending them — the store-level reads and sequences.

Each test below pins one store-level decision whose reversion no other test caught."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store.errors import RunnerStoreError, RunnerStoreErrorFactory
from blizzard.runner.store.schema import (
    binding_releases,
    env_bindings,
    leases,
    park_facts,
    pause_park_resumes,
    pause_parks,
    requeues,
)
from tests.runner_fakes import FIXTURE_RUNNER_ID, SqlAlchemyRunnerStore, make_store
from tests.support_pin_store import NOW


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


def _seqs(store: SqlAlchemyRunnerStore, table: sa.Table) -> list[int]:
    """The ``seq`` of each of ``table``'s rows, in the order its rows were appended."""
    with store.engine.connect() as conn:
        return list(
            conn.execute(
                sa.select(table.c.seq).where(table.c.runner_id == FIXTURE_RUNNER_ID).order_by(table.c.seq)
            ).scalars()
        )


def _mint(store: SqlAlchemyRunnerStore, lease_id: str, *, epoch: int = 1, at: datetime = NOW) -> None:
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


def test_the_driver_error_wrap_is_substitutable_by_injection(tmp_path: Path) -> None:
    """``_wrap`` is an injected ``RunnerStoreErrorFactory``, not a module-level logger
    — a fake factory observes the wrap without reaching into the module."""
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")  # unmigrated: every query is a driver fault
    errors = _FakeRunnerStoreErrors()
    store = SqlAlchemyRunnerStore(engine, errors)

    with pytest.raises(RunnerStoreError):
        store.pending_outbound()

    assert errors.wrapped == ["query"]


def test_a_rebind_after_a_release_reads_as_held(tmp_path: Path):
    """Bind -> release -> bind again on the same ``(chunk, env)``: the second binding
    is held, not read as still-released by a naive set-difference predicate."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=NOW + timedelta(minutes=1))
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=NOW + timedelta(minutes=2))

    assert store.held_environment_ids() == ["e1"]
    assert store.live_tenure_chunk_ids() == ["ch_1"]
    assert [b.environment_id for b in store.bindings_for_chunk("ch_1")] == ["e1"]


def test_a_same_instant_rebind_after_a_release_reads_as_held(tmp_path: Path):
    """Bind -> release -> bind at one instant: the release closes only the bind appended before it, so the rebind is
    held — a comparison of instants would have the release close both."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=NOW)
    assert store.held_environment_ids() == []
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=NOW)

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


def test_a_same_instant_pause_resume_pause_leaves_the_second_park_open(tmp_path: Path):
    """Pause -> resume -> pause under one lease at one instant: the resume closes only the park appended before it."""
    store = _store(tmp_path)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=NOW)
    store.record_pause_park_resume(lease_id="lease_1", resumed_at=NOW)
    assert store.pause_parked_lease_ids() == set()
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=NOW)

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


def test_the_newest_binding_of_a_chunk_reads_through_its_release(tmp_path: Path):
    """A lease's workspace is read from its newest binding row, released or not, and never from another chunk's."""
    store = _store(tmp_path)
    assert store.newest_binding_for_chunk("ch_1") is None
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/a/e1", bound_at=NOW, workspace_id="ws_a")
    store.record_binding(
        chunk_id="ch_1", environment_id="e1", workdir="/b/e1", bound_at=NOW + timedelta(minutes=1), workspace_id="ws_b"
    )
    store.record_binding(chunk_id="ch_2", environment_id="e2", workdir="/a/e2", bound_at=NOW, workspace_id="ws_a")
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=NOW + timedelta(minutes=2))

    newest = store.newest_binding_for_chunk("ch_1")
    assert store.bindings_for_chunk("ch_1") == []
    assert newest is not None
    assert (newest.workspace_id, newest.workdir) == ("ws_b", "/b/e1")


def test_the_newest_binding_of_a_chunk_is_the_latest_recorded_even_at_an_equal_instant(tmp_path: Path):
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/a/e1", bound_at=NOW, workspace_id="ws_a")
    store.record_binding(chunk_id="ch_1", environment_id="e2", workdir="/b/e2", bound_at=NOW)

    newest = store.newest_binding_for_chunk("ch_1")
    assert newest is not None
    assert (newest.environment_id, newest.workspace_id) == ("e2", None)


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
                created_at=NOW,
            )
        )
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=NOW)
    with store.engine.connect() as conn:
        floor = conn.execute(
            sa.select(binding_releases.c.lease_epoch_floor).where(binding_releases.c.runner_id == FIXTURE_RUNNER_ID)
        ).scalar_one()
    assert floor == 1
    assert not store.has_lease_in_binding_tenure("ch_1", NOW)
    assert store.has_lease_in_binding_tenure("ch_2", NOW)
    assert store.has_lease_in_binding_tenure("ch_3", NOW)
    assert not store.has_lease_in_binding_tenure("ch_2", NOW + timedelta(seconds=1))
    assert not store.has_lease_in_binding_tenure("ch_missing", NOW)

    store.record_lease(
        NewLease(
            lease_id="lease_ch_1_new",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_retro",
            node_name="retro",
            epoch=2,
            retries_max=2,
            created_at=NOW,
        )
    )
    assert store.has_lease_in_binding_tenure("ch_1", NOW)
    assert not store.has_lease_in_binding_tenure("ch_1", NOW + timedelta(seconds=1))


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
            created_at=NOW,
        )
    )
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=NOW)
    store.record_escalation_closure(chunk_id="ch_1", reason="stopped", at=NOW)  # the same instant

    assert [e.lease_id for e in store.open_escalations()] == ["lease_1"]

    store.record_escalation_closure(chunk_id="ch_1", reason="stopped", at=NOW + timedelta(seconds=1))

    assert store.open_escalations() == []


def test_a_same_instant_mint_consumes_its_requeue_mark(tmp_path: Path):
    """A spawn that stamps its lease at the requeue mark's own instant still consumes it, as it is appended after
    the mark — a comparison of instants that left it pending would never see the reattempt happen."""
    store = _store(tmp_path)
    store.record_requeue(chunk_id="ch_1", at=NOW)
    assert store.pending_requeue_chunk_ids() == {"ch_1"}

    _mint(store, "lease_1")  # the same instant the mark carries

    assert store.pending_requeue_chunk_ids() == set()
    assert _seqs(store, requeues) < _seqs(store, leases)


def test_a_same_instant_mint_appended_before_a_requeue_mark_leaves_it_pending(tmp_path: Path):
    """A mark appended after a mint is not that mint's to consume, whatever instant both carry: the reattempt it asks
    for is still to come."""
    store = _store(tmp_path)
    _mint(store, "lease_1")
    store.record_requeue(chunk_id="ch_1", at=NOW)

    assert store.pending_requeue_chunk_ids() == {"ch_1"}
    assert _seqs(store, leases) < _seqs(store, requeues)

    _mint(store, "lease_2", epoch=2)

    assert store.pending_requeue_chunk_ids() == set()


def test_fact_sequences_are_one_runner_wide_across_the_tables(tmp_path: Path):
    """One counter numbers every sequenced table's rows, so rows of different tables are ordered by it."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=NOW)
    _mint(store, "lease_1")
    store.record_requeue(chunk_id="ch_1", at=NOW)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=NOW)

    assert [
        _seqs(store, env_bindings),
        _seqs(store, leases),
        _seqs(store, requeues),
        _seqs(store, pause_parks),
    ] == [[1], [2], [3], [4]]


def test_pause_parks_are_their_own_table_and_park_facts_keeps_a_non_null_question_id() -> None:
    """The pause-park split, structurally: ``park_facts.question_id`` stays NOT NULL
    and ``pause_parks`` carries no ``question_id`` at all — one NULL in the
    ``unforwarded_ask`` subquery would break ask-and-exit fleet-wide."""
    assert park_facts.c.question_id.nullable is False
    assert "question_id" not in pause_parks.c


def test_set_route_token_keeps_one_current_row_per_chunk(tmp_path: Path):
    """A re-claim of the same chunk overwrites the stashed plaintext rather than appending:
    the runner only ever presents its *current* token, so there is exactly one row to read
    and no rotation history to disambiguate."""
    store = _store(tmp_path)
    store.set_route_token("ch_1", token="rtok-old", at=NOW)
    store.set_route_token("ch_1", token="rtok-new", at=NOW + timedelta(minutes=1))

    assert store.route_token("ch_1") == "rtok-new"
