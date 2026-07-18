"""``blizzard runner requeue`` — the domain service + loop resume (component tier, issue #53).

Drives :class:`RequeueService` directly against a real tmp store (``bzh:steppable-loop``
convention, mirroring ``tests/test_runner_takeover.py``): a chunk parked needs_human via
an escalated lease closure requeues cleanly and leaves the escalation open (a requeue
mark alone supersedes nothing — only the fresh mint that follows does); an open takeover
refuses with a 409-mapped error; a chunk carrying no open escalation refuses too; and the
pasted-command flow (an ended takeover with no requeue-blocking effect) requeues exactly
like a bare escalation. A second slice drives FILL against a pending requeue mark to pin
the fresh-attempt spawn — new lease, new epoch, carried retry budget — and its consumption.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import DEFAULT_MODEL, ChunkStatus
from blizzard.runner.domain.requeue import ChunkNotRequeueable, RequeueBlockedByOpenTakeover, RequeueService
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import fill
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, RouteView
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_envelope, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2026, 7, 17, 13, 0, 0, tzinfo=UTC)
_EVEN_LATER = datetime(2026, 7, 17, 14, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _service(store, *, clock=None):  # type: ignore[no-untyped-def]
    return RequeueService(store, clock or FixedClock(_LATER))


def _seed_escalated_chunk(store, *, chunk="ch_1", lease="lease_1", node_id="nd_build", node_name="build", epoch=1):  # type: ignore[no-untyped-def]
    """A build lease, spawned, bound, then closed escalated — retries exhausted."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease, pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_closure(lease_id=lease, chunk_id=chunk, node_id=node_id, reason="escalated", closed_at=_NOW)


# --------------------------------------------------------------------------- #
# The domain service — happy path, the two 409s, and the pasted-command flow
# --------------------------------------------------------------------------- #


def test_requeue_appends_a_clearing_fact_and_leaves_the_escalation_open(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_escalated_chunk(store)

    _service(store).requeue("ch_1")

    assert "ch_1" in store.pending_requeue_chunk_ids()
    # The mark alone supersedes nothing — only a later lease mint does (the hub's own
    # derivation, mirrored locally): the escalation reads open until FILL's fresh spawn.
    assert store.open_escalation_for_chunk("ch_1") is not None


def test_requeue_refuses_while_a_takeover_is_open(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )

    with pytest.raises(RequeueBlockedByOpenTakeover):
        _service(store).requeue("ch_1")

    assert store.pending_requeue_chunk_ids() == set()


def test_requeue_refuses_a_chunk_that_is_not_needs_human(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )  # active — no closure at all, nothing needs_human

    with pytest.raises(ChunkNotRequeueable):
        _service(store).requeue("ch_1")

    assert store.pending_requeue_chunk_ids() == set()


def test_requeue_works_after_an_ended_takeover_with_no_recorded_escalation_change(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The pasted-command flow: a takeover opened, worked, and ended — the closure
    reason underneath never changed, so the same needs_human read covers it."""
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_takeover_end(takeover_id="tko_1", ended_at=_NOW)

    _service(store).requeue("ch_1")

    assert "ch_1" in store.pending_requeue_chunk_ids()


def test_requeue_a_chunk_with_no_recorded_takeover_at_all(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The pasted-command flow with no takeover ever recorded — a human resumed the
    escalation's surfaced command by hand and fixed it in place."""
    store = _store(tmp_path)
    _seed_escalated_chunk(store)

    _service(store).requeue("ch_1")

    assert "ch_1" in store.pending_requeue_chunk_ids()
    assert store.open_takeover_for_chunk("ch_1") is None


# --------------------------------------------------------------------------- #
# FILL — the fresh attempt spawned after a pending requeue mark, and its consumption
# --------------------------------------------------------------------------- #


def test_fill_spawns_a_fresh_attempt_after_requeue_and_consumes_the_mark(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    _service(store).requeue("ch_1")

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

    fill(ctx)

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


def test_fill_releases_the_binding_when_a_requeued_chunk_is_no_longer_routed_here(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_escalated_chunk(store)
    _service(store).requeue("ch_1")

    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.NEEDS_HUMAN,
        current_node_id="nd_build",
        latest_epoch=1,
        model=DEFAULT_MODEL,
        route=RouteView(runner_id="some-other-runner", workspace_id="ws1", environment_ids=["e1"]),
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

    fill(ctx)

    assert harness.spawns == []
    assert store.held_environment_ids() == []
