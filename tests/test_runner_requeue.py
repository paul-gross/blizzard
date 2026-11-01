"""``blizzard runner requeue`` — the domain service + loop resume (component tier, #53).

Drives :class:`RequeueService` against a real tmp store: an escalated closure requeues
cleanly, leaving the escalation open; an open takeover or no escalation refuses (409).
FILL against a pending mark spawns a fresh attempt — new lease, epoch, carried retries.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Row, Select, create_engine, select

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.steps import Fill
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.operator.requeue import (
    ChunkNotRequeueable,
    RequeueBlockedByOpenTakeover,
    RequeueScope,
    RequeueService,
)
from blizzard.runner.store.schema import leases, requeues
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2026, 7, 17, 13, 0, 0, tzinfo=UTC)
_EVEN_LATER = datetime(2026, 7, 17, 14, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200", pgid=200)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _service(store: SqlAlchemyRunnerStore, *, clock: FixedClock | None = None) -> RequeueService:
    return RequeueService(store, clock or FixedClock(_LATER))


def _scope(store: SqlAlchemyRunnerStore, chunk_id: str = "ch_1") -> RequeueScope:
    """The edge resolution `chunk_scope.resolved_requeue_scope` performs in production."""
    return RequeueScope(
        chunk_id=chunk_id,
        open_takeover=store.open_takeover_for_chunk(chunk_id),
        open_escalation=store.open_escalation_for_chunk(chunk_id),
        held_environment_ids=tuple(b.environment_id for b in store.bindings_for_chunk(chunk_id)),
        requeue_pending=chunk_id in store.pending_requeue_chunk_ids(),
    )


def _seed_escalated_chunk(
    store: SqlAlchemyRunnerStore,
    *,
    chunk: str = "ch_1",
    lease: str = "lease_1",
    node_id: str = "nd_build",
    node_name: str = "build",
    epoch: int = 1,
    harness_id: str = CLAUDE_CODE_HARNESS_ID,
) -> None:
    """A build lease, spawned, bound, then closed escalated — retries exhausted."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=100,
        process_start_time="start-100",
        session=SessionReference(harness_id, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_closure(lease_id=lease, chunk_id=chunk, node_id=node_id, reason="escalated", closed_at=_NOW)


# The domain service — happy path, the two 409s, and the pasted-command flow


def test_requeue_appends_a_clearing_fact_and_leaves_the_escalation_open(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_escalated_chunk(store)

    _service(store).requeue(_scope(store))

    assert "ch_1" in store.pending_requeue_chunk_ids()
    # The mark alone supersedes nothing — only a later lease mint does (the hub's own
    # derivation, mirrored locally): the escalation reads open until FILL's fresh spawn.
    assert store.open_escalation_for_chunk("ch_1") is not None


def test_requeue_refuses_while_a_takeover_is_open(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )

    with pytest.raises(RequeueBlockedByOpenTakeover):
        _service(store).requeue(_scope(store))

    assert store.pending_requeue_chunk_ids() == set()


def test_requeue_refuses_a_chunk_that_is_not_needs_human(tmp_path: Path) -> None:
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
    )  # active — no closure at all, nothing needs_human

    with pytest.raises(ChunkNotRequeueable):
        _service(store).requeue(_scope(store))

    assert store.pending_requeue_chunk_ids() == set()


def test_requeue_works_after_an_ended_takeover_with_no_recorded_escalation_change(tmp_path: Path) -> None:
    """The pasted-command flow: a takeover opened, worked, and ended — the closure
    reason underneath never changed, so the same needs_human read covers it."""
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_takeover_end(takeover_id="tko_1", ended_at=_NOW)

    _service(store).requeue(_scope(store))

    assert "ch_1" in store.pending_requeue_chunk_ids()


def test_requeue_a_chunk_with_no_recorded_takeover_at_all(tmp_path: Path) -> None:
    """An escalated chunk with no takeover ever recorded still takes a requeue mark."""
    store = _store(tmp_path)
    _seed_escalated_chunk(store)

    _service(store).requeue(_scope(store))

    assert "ch_1" in store.pending_requeue_chunk_ids()
    assert store.open_takeover_for_chunk("ch_1") is None


# FILL — the fresh attempt spawned after a pending requeue mark, and its consumption


def test_fill_spawns_a_fresh_attempt_after_requeue_and_consumes_the_mark(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    _service(store).requeue(_scope(store))

    hub = FakeHub()
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok"), ("fail", "no")])
    hub.envelopes["ch_1"] = envelope
    hub.queue = []  # nothing new to fill — only the requeue-resume path should act
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_EVEN_LATER),  # strictly after the requeue mark — a real fresh mint
    )

    Fill(ctx).run()

    assert len(harness.spawns) == 1
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None
    assert fresh.lease_id != "lease_1"  # a new lease, not a resume of the old one
    assert fresh.session_id == "sess-b"  # the harness's fresh spawn handle
    assert fresh.epoch == 2  # bumped past the escalated attempt's epoch
    # Carried, not reset: attempt_count now counts both the original escalated mint and
    # this fresh one against the same node's unchanged retries_max.
    assert store.attempt_count("ch_1", "nd_build") == 2
    # Consumed: the mark's chunk no longer reads pending once the fresh lease landed.
    assert store.pending_requeue_chunk_ids() == set()
    # No route re-claim — the chunk never re-entered the hub's queue.
    assert hub.claims == []


def test_fill_requeue_resume_carries_the_failed_leases_own_harness_owner(tmp_path: Path) -> None:
    """The requeue-resume path (``InterruptedClaims._resume_requeued``) mints its fresh
    lease under the escalated lease's recorded owner (``_latest_owner`` in ``src/blizzard/runner/lifecycle/claim.py``)
    — never the default harness, even though a fresh mint carries no session yet."""
    store = _store(tmp_path)
    _seed_escalated_chunk(store, harness_id="other")
    _service(store).requeue(_scope(store))

    hub = FakeHub()
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok"), ("fail", "no")])
    hub.envelopes["ch_1"] = envelope
    hub.queue = []  # nothing new to fill — only the requeue-resume path should act
    default = FakeHarness(handle=_HANDLE, verdict=None)
    other = FakeHarness(
        handle=WorkerHandle(session_id="sess-c", pid=300, process_start_time="start-300", pgid=300), verdict=None
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=default,
        probe=FakeProbe(),
        clock=FixedClock(_EVEN_LATER),  # strictly after the requeue mark — a real fresh mint
    )
    registry = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=default, transcript_source=default.transcript_source()),
            "other": HarnessBinding(adapter=other, transcript_source=other.transcript_source()),
        }
    )
    ctx = replace(
        ctx,
        harnesses=registry,
        sessions=replace(ctx.sessions, harnesses=registry),
        usage=replace(ctx.usage, harnesses=registry),
    )

    Fill(ctx).run()

    assert default.spawns == []
    assert len(other.spawns) == 1
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.session == SessionReference("other", "sess-c")


def test_fill_releases_the_binding_when_a_requeued_chunk_is_no_longer_routed_here(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    _service(store).requeue(_scope(store))

    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkState(
        chunk_id="ch_1",
        status=ChunkStatus.NEEDS_HUMAN,
        latest_epoch=1,
        route_runner_id="some-other-runner",
    )
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_EVEN_LATER),  # strictly after the binding's bound_at
    )

    Fill(ctx).run()

    assert harness.spawns == []
    assert store.held_environment_ids() == []


def _rows[T: tuple[object, ...]](tmp_path: Path, stmt: Select[T]) -> Sequence[Row[T]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    try:
        with engine.connect() as conn:
            return conn.execute(stmt).all()
    finally:
        engine.dispose()


def test_a_requeue_asked_while_one_is_pending_changes_nothing(tmp_path: Path) -> None:
    """One mark, untouched by the second ask; one fill, one lease, one retry charged."""
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    marks = select(requeues.c.requeued_at, requeues.c.seq).where(requeues.c.runner_id == FIXTURE_RUNNER_ID)
    _service(store, clock=FixedClock(_LATER)).requeue(_scope(store))
    written = _rows(tmp_path, marks)
    _service(store, clock=FixedClock(_LATER + (_EVEN_LATER - _LATER) / 2)).requeue(_scope(store))
    assert len(written) == 1
    assert _rows(tmp_path, marks) == written
    assert store.pending_requeue_chunk_ids() == {"ch_1"}

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok"), ("fail", "no")])
    hub.queue = []
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_EVEN_LATER),
    )

    Fill(ctx).run()
    Fill(ctx).run()

    assert len(harness.spawns) == 1
    assert store.pending_requeue_chunk_ids() == set()
    assert store.attempt_count("ch_1", "nd_build") == 2
    assert (
        len(
            _rows(
                tmp_path,
                select(leases.c.lease_id).where(leases.c.chunk_id == "ch_1", leases.c.runner_id == FIXTURE_RUNNER_ID),
            )
        )
        == 2
    )
    assert hub.claims == []
