"""A local open takeover whose chunk the hub has since ended (issue #291).

Under D1 the open-takeover fact authorizes a resumed session's worker verbs, so a chunk
the hub ends mid-takeover must not leave that authorization standing forever.
``Pull._reconcile_takeovers`` folds into PULL — the same shape as
``_reconcile_escalations`` (#292) — to mirror the hub's terminal answer as a
``takeover_ends`` mark, the second, no-person-drives closer alongside the CLI's own
end-PATCH."""

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

_NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_taken_over(store, *, chunk="ch_1", lease="lease_1", epoch=1, at=_NOW):  # type: ignore[no-untyped-def]
    """A lease minted, closed ``escalated``, then taken over — the needs-human-under-
    takeover shape a stranded operator session leaves behind."""
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
    store.record_takeover(
        takeover_id=f"tko_{lease}",
        chunk_id=chunk,
        lease_id=lease,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=at,
    )


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
def test_pull_closes_a_takeover_the_hub_stopped(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_taken_over(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _chunk(status=ChunkStatus.STOPPED)
    assert store.open_takeover_chunk_ids() == {"ch_1"}

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert store.open_takeover_chunk_ids() == set()
    assert store.open_takeover_for_chunk("ch_1") is None


@pytest.mark.unit
def test_pull_closes_a_takeover_whose_chunk_the_hub_landed(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_taken_over(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _chunk(status=ChunkStatus.DONE)

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert store.open_takeover_for_chunk("ch_1") is None


@pytest.mark.unit
def test_pull_leaves_a_takeover_the_hub_still_holds(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_taken_over(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _chunk(status=ChunkStatus.NEEDS_HUMAN)

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert store.open_takeover_for_chunk("ch_1") is not None


@pytest.mark.unit
def test_an_unreachable_hub_leaves_the_takeover_open(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_taken_over(store)
    hub = FakeHub()
    hub.down = True

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert store.open_takeover_for_chunk("ch_1") is not None


@pytest.mark.unit
def test_a_chunk_the_hub_does_not_know_is_not_a_resolution(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_taken_over(store)
    hub = FakeHub()
    hub.not_found.add("ch_1")

    Pull(_ctx(store, hub, clock=FixedClock(_NOW + timedelta(minutes=5)))).run()

    assert store.open_takeover_for_chunk("ch_1") is not None
