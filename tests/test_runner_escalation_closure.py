"""A local escalation the hub has since stopped (#292).

An escalated lease is already closed, so ``_reconcile_leases`` never revisits it and the
only local supersession is a later lease mint a stopped chunk never gets. ``_reconcile_escalations``
folds into PULL to mirror the hub's terminal answer as an ``escalation_closures`` mark."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import Pull
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, RouteView
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_escalated(store, *, chunk="ch_1", lease="lease_1", epoch=1, at=_NOW):  # type: ignore[no-untyped-def]
    """A lease minted, spawned, then closed ``escalated`` — the parked needs-human shape,
    with no later mint to supersede it."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=at,
        )
    )
    store.record_spawn(lease, pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=at)
    store.record_closure(lease_id=lease, chunk_id=chunk, node_id="nd_build", reason="escalated", closed_at=at)


def _chunk(chunk="ch_1", *, status: ChunkStatus):  # type: ignore[no-untyped-def]
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=status,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
    )


def _ctx(store, hub, *, clock=None):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(alive=set()),
        clock=clock,
    )


@pytest.mark.unit
def test_pull_closes_an_escalation_the_hub_stopped(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_escalated(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _chunk(status=ChunkStatus.STOPPED)
    assert [e.chunk_id for e in store.open_escalations()] == ["ch_1"]

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert store.open_escalations() == []
    assert store.open_escalation_for_chunk("ch_1") is None


@pytest.mark.unit
def test_pull_closes_an_escalation_whose_chunk_the_hub_landed(tmp_path):  # type: ignore[no-untyped-def]
    # The chunk was requeued away and landed by another runner (#293): no later lease is
    # minted here, so `done` is the only arm that can close this box's escalation.
    store = _store(tmp_path)
    _seed_escalated(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _chunk(status=ChunkStatus.DONE)

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert store.open_escalations() == []


@pytest.mark.unit
def test_pull_leaves_an_escalation_the_hub_still_holds(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_escalated(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _chunk(status=ChunkStatus.NEEDS_HUMAN)

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert [e.chunk_id for e in store.open_escalations()] == ["ch_1"]


@pytest.mark.unit
def test_an_unreachable_hub_leaves_the_escalation_open(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_escalated(store)
    hub = FakeHub()
    hub.down = True

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert [e.chunk_id for e in store.open_escalations()] == ["ch_1"]


@pytest.mark.unit
def test_a_chunk_the_hub_does_not_know_is_not_a_resolution(tmp_path):  # type: ignore[no-untyped-def]
    # A 404 is not the hub asserting the hold is resolved — the escalation stands.
    store = _store(tmp_path)
    _seed_escalated(store)
    hub = FakeHub()
    hub.not_found.add("ch_1")

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert [e.chunk_id for e in store.open_escalations()] == ["ch_1"]


@pytest.mark.unit
def test_a_re_escalation_after_a_close_reads_open_again(tmp_path):  # type: ignore[no-untyped-def]
    # The mark supersedes only escalations that precede it — strict ``>``, so a later
    # escalation on a fresh lease is never masked by an older close.
    store = _store(tmp_path)
    _seed_escalated(store)
    store.record_escalation_closure(chunk_id="ch_1", reason="stopped", at=_NOW + timedelta(minutes=5))
    assert store.open_escalations() == []

    _seed_escalated(store, lease="lease_2", epoch=2, at=_NOW + timedelta(minutes=10))

    assert [e.lease_id for e in store.open_escalations()] == ["lease_2"]
