"""The fact lane's drain (component tier) — contiguous generic-kind runs batched into one
``push_facts`` call each, a completion or decision fact first flushing the run collected so
far and then still handled one at a time by its own arm."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop import drain as drain_module
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.drain import OutboundDrain
from blizzard.runner.loop.outbound import COMPLETION_KIND
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
    runner_invariant_violations,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)

_COMPLETION_PAYLOAD = json.dumps(
    {
        "submission": {
            "choice": "APPROVE",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": "nd_build",
            "check_results": [],
            "artifacts": [],
            "proposals": [],
            "decision_id": None,
            "route_token": None,
        }
    }
)


def _ctx(hub: FakeHub, *, store=None):  # type: ignore[no-untyped-def]
    store = store if store is not None else make_store("sqlite://")
    harness = FakeHarness(handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"), verdict=None)
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1"),
        clock=FixedClock(_NOW),
    )


def _enqueue_generic(ctx, *, kind: str = "event.recorded", chunk_id: str = "ch_1") -> int:  # type: ignore[no-untyped-def]
    return ctx.stores.outbound.enqueue_outbound(
        kind=kind, chunk_id=chunk_id, lease_id=None, payload="{}", created_at=_NOW
    )


def _enqueue_completion(ctx, *, chunk_id: str = "ch_1", lease_id: str = "lease_gone") -> int:  # type: ignore[no-untyped-def]
    """Buffers a completion whose lease never exists locally — `_completion` reads that as
    already advanced on an earlier flush and returns early, so this drives the drain's own
    run-then-arm sequencing without standing up a full lease/attempt fixture."""
    return ctx.stores.outbound.enqueue_outbound(
        kind=COMPLETION_KIND, chunk_id=chunk_id, lease_id=lease_id, payload=_COMPLETION_PAYLOAD, created_at=_NOW
    )


def test_run_sends_a_contiguous_run_of_generic_facts_in_one_push_facts_call() -> None:
    hub = FakeHub()
    ctx = _ctx(hub)
    seqs = [_enqueue_generic(ctx) for _ in range(5)]

    OutboundDrain(ctx).run()

    assert len(hub.push_facts_calls) == 1
    assert hub.push_facts_calls[0] == seqs
    assert ctx.stores.outbound.pending_outbound() == []


def test_run_flushes_the_collected_run_before_a_completion_then_handles_it_on_its_own() -> None:
    hub = FakeHub()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, detail="")]
    ctx = _ctx(hub)
    run_seqs = [_enqueue_generic(ctx) for _ in range(3)]
    completion_seq = _enqueue_completion(ctx)
    trailing_seqs = [_enqueue_generic(ctx) for _ in range(2)]

    OutboundDrain(ctx).run()

    # The run ahead of the completion ships as one push_facts call...
    assert hub.push_facts_calls[0] == run_seqs
    # ...the completion is submitted through its own arm, not folded into any push_facts call...
    assert len(hub.completions) == 1
    assert all(completion_seq not in call for call in hub.push_facts_calls)
    # ...and the trailing run, collected after it, ships as its own separate call.
    assert hub.push_facts_calls[1] == trailing_seqs
    assert len(hub.push_facts_calls) == 2
    assert ctx.stores.outbound.pending_outbound() == []


def test_run_bounds_its_own_per_run_slice_and_drains_a_larger_backlog_over_several_ticks() -> None:
    hub = FakeHub()
    store = make_store("sqlite://")
    ctx = _ctx(hub, store=store)
    total = drain_module._DRAIN_LIMIT + 5
    seqs = [_enqueue_generic(ctx) for _ in range(total)]

    OutboundDrain(ctx).run()
    assert len(ctx.stores.outbound.pending_outbound()) == 5  # the rest waits for the next tick

    OutboundDrain(ctx).run()
    assert ctx.stores.outbound.pending_outbound() == []

    # Every seq landed at the hub exactly once, in order, across the two ticks' calls.
    delivered = [seq for call in hub.push_facts_calls for seq in call]
    assert delivered == seqs
    assert runner_invariant_violations(store) == []


def test_run_stops_the_whole_run_on_a_transport_failure_and_retries_it_next_tick() -> None:
    hub = FakeHub()
    hub.down = True
    ctx = _ctx(hub)
    seqs = [_enqueue_generic(ctx) for _ in range(3)]

    OutboundDrain(ctx).run()

    assert hub.push_facts_calls == []
    assert [f.seq for f in ctx.stores.outbound.pending_outbound()] == seqs  # nothing acked

    hub.down = False
    OutboundDrain(ctx).run()

    assert hub.push_facts_calls == [seqs]
    assert ctx.stores.outbound.pending_outbound() == []


def test_run_acks_a_rejected_fact_within_its_run_rather_than_wedging_the_fifo() -> None:
    hub = FakeHub()
    ctx = _ctx(hub)
    seqs = [_enqueue_generic(ctx) for _ in range(3)]

    real_push_facts = hub.push_facts

    def _reject_middle(batch):  # type: ignore[no-untyped-def]
        ack = real_push_facts(batch)
        return ack.model_copy(update={"applied": [s for s in ack.applied if s != seqs[1]], "rejected": [seqs[1]]})

    hub.push_facts = _reject_middle  # type: ignore[method-assign]

    OutboundDrain(ctx).run()

    # A contract rejection is not idempotency — the whole run, rejected fact included, is
    # still acked in one transaction so the FIFO drain never wedges on it.
    assert ctx.stores.outbound.pending_outbound() == []
