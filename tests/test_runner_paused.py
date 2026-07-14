"""The runner adheres to the hub's pause brake (D-043/D-012) — loop unit tier.

The declarative pause brake lives at the hub; the runner reads it on PULL (a
``GET /runners/{id}`` behind the hub client), mirrors it to its store, and FILL adheres:
paused = no new claims, in-flight chunks run on. When the hub is unreachable the runner
keeps its last-mirrored directive (D-012). Driven directly against a real tmp store with
a :class:`FakeHub` whose ``paused`` flag the test flips.
"""

from __future__ import annotations

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import fill, pull
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _ctx_with_a_claimable_chunk(tmp_path, *, paused: bool):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.paused = paused
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )
    return ctx, hub, store


@pytest.mark.unit
def test_pull_mirrors_the_hub_pause_brake_and_registers(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    pull(ctx)
    # PULL registered the runner (liveness heartbeat) and mirrored the brake locally.
    assert hub.registered == [("r1", "ws1")]
    assert store.hub_paused("r1") is True


@pytest.mark.unit
def test_fill_claims_nothing_while_paused(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    pull(ctx)  # mirror paused=True
    fill(ctx)
    # No claim was attempted and no lease was minted — the queue is untouched.
    assert hub.claims == []
    assert store.list_active_leases() == []


@pytest.mark.unit
def test_fill_claims_again_after_resume(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    pull(ctx)
    fill(ctx)
    assert store.list_active_leases() == []

    # The operator resumes the runner; the next PULL mirrors it and FILL claims.
    hub.paused = False
    pull(ctx)
    fill(ctx)
    assert len(hub.claims) == 1
    assert len(store.list_active_leases()) == 1


@pytest.mark.unit
def test_in_flight_chunk_runs_on_while_paused(tmp_path):  # type: ignore[no-untyped-def]
    """Pausing stops new claims; an already-claimed chunk is untouched by FILL."""
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    pull(ctx)
    fill(ctx)  # claims ch_1
    assert len(store.list_active_leases()) == 1

    # Now pause; FILL must not tear down or re-claim — the in-flight lease persists.
    hub.paused = True
    pull(ctx)
    fill(ctx)
    assert len(store.list_active_leases()) == 1


@pytest.mark.unit
def test_unreachable_hub_keeps_last_mirrored_brake(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    pull(ctx)  # mirror paused=True
    assert store.hub_paused("r1") is True

    # The hub goes unreachable; PULL cannot refresh, so the last-known brake holds.
    hub.down = True
    pull(ctx)
    assert store.hub_paused("r1") is True
    fill(ctx)
    assert hub.claims == []  # still adhering to the last directive (D-012)
