"""The per-tick chunk-status cache (blizzard#521) — ``tick()``'s own read hoist onto
``IHubClient.chunk_statuses``, proven at the seam :mod:`blizzard.runner.loop.chunk_status_cache`
adds: one hub round-trip per distinct chunk id per tick, a write this same tick invalidates
(D5), and an unknown id raising ``ChunkNotFoundError`` at every reader without a repeat read.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.outbound import COMPLETION_KIND
from blizzard.runner.loop.tick import tick
from blizzard.wire.chunk import ChunkStatusView, ChunkUsageTotalView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.facts import ESCALATION_RECORDED, EVENT_RECORDED
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_active_lease(store, *, chunk: str, lease: str, pid: int, start: str):  # type: ignore[no-untyped-def]
    """A running lease with a live worker — no bearing on the cache other than being one
    of the ids every ``Reap``/``Pull``/``Advance`` sweep reads this tick."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=pid,
        process_start_time=start,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{chunk}"),
        spawned_at=_NOW,
    )


def _seed_escalated(store, *, chunk: str, lease: str):  # type: ignore[no-untyped-def]
    """A lease minted, spawned, then closed ``escalated`` — the open-escalation shape."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=200,
        process_start_time="start-200",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{chunk}"),
        spawned_at=_NOW,
    )
    store.record_closure(lease_id=lease, chunk_id=chunk, node_id="nd_build", reason="escalated", closed_at=_NOW)


def _seed_taken_over(store, *, chunk: str, lease: str):  # type: ignore[no-untyped-def]
    """A lease minted, closed ``escalated``, then taken over — the open-takeover shape."""
    _seed_escalated(store, chunk=chunk, lease=lease)
    store.record_takeover(
        takeover_id=f"tko_{lease}",
        chunk_id=chunk,
        lease_id=lease,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{chunk}"),
        workdir="/ws/e_tko",
        fence_epoch=None,
        opened_at=_NOW,
    )


def test_tick_primes_every_touched_chunk_in_one_batch_call(tmp_path):  # type: ignore[no-untyped-def]
    """One tick, five distinct touched chunks — an active lease, an open escalation, an
    open takeover, a held lease-less chunk, and a held chunk the hub no longer knows —
    costs exactly one ``chunk_statuses`` call naming all five, never a repeat read for any
    one of them (blizzard#521's whole point)."""
    store = _store(tmp_path)
    _seed_active_lease(store, chunk="ch_lease", lease="lease_1", pid=100, start="start-100")
    _seed_escalated(store, chunk="ch_esc", lease="lease_2")
    _seed_taken_over(store, chunk="ch_tko", lease="lease_3")
    store.record_binding(chunk_id="ch_held", environment_id="e_held", workdir="/ws/e_held", bound_at=_NOW)
    store.record_binding(chunk_id="ch_unknown", environment_id="e_unknown", workdir="/ws/e_unknown", bound_at=_NOW)

    hub = FakeHub()
    hub.queue = []  # nothing to claim — keep FILL's own reads confined to the fixture
    hub.chunks["ch_lease"] = ChunkStatusView(chunk_id="ch_lease", status=ChunkStatus.RUNNING, route_runner_id="r1")
    # NEEDS_HUMAN + still routed here — neither escalation nor takeover reads as superseded,
    # so PULL's reconcile sweeps leave them exactly as scripted, with no writes of their own.
    hub.chunks["ch_esc"] = ChunkStatusView(chunk_id="ch_esc", status=ChunkStatus.NEEDS_HUMAN, route_runner_id="r1")
    hub.chunks["ch_tko"] = ChunkStatusView(chunk_id="ch_tko", status=ChunkStatus.NEEDS_HUMAN, route_runner_id="r1")
    # WAITING_ON_HUMAN: none of HeldChunk.drive's branches fire — a pure no-op poll.
    hub.chunks["ch_held"] = ChunkStatusView(
        chunk_id="ch_held", status=ChunkStatus.WAITING_ON_HUMAN, route_runner_id="r1"
    )
    hub.not_found = {"ch_unknown"}

    provider = FakeProvider({"e_held": "/ws/e_held", "e_unknown": "/ws/e_unknown"})
    probe = FakeProbe(alive={(100, "start-100"), (200, "start-200")})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict=None), probe=probe
    )

    tick(ctx)

    assert len(hub.chunk_statuses_calls) == 1, hub.chunk_statuses_calls
    assert set(hub.chunk_statuses_calls[0]) == {"ch_lease", "ch_esc", "ch_tko", "ch_held", "ch_unknown"}
    # Two independent readers touch the not-found id this tick (FILL's interrupted-claim
    # reconciler and ADVANCE's held-chunk poll) — both raised ChunkNotFoundError off the
    # ONE cached absence above, each releasing the same way a genuine 404 always has;
    # the single batch call above is the proof the cache — not the hub — absorbed the repeat.
    assert provider.released == ["e_unknown", "e_unknown"]
    # The known-but-inert chunks were left exactly as scripted — no chunk here was written to.
    assert store.open_escalations() != []
    assert store.open_takeover_for_chunk("ch_tko") is not None


class _WriteReactingHub(FakeHub):
    """A :class:`FakeHub` whose ``push_facts``/``submit_completion`` also flip a chunk's
    scripted status — standing in for the hub's own reaction to the write landing, the
    signal a reader after invalidation must see and a stale cache would have missed."""

    def push_facts(self, batch):  # type: ignore[no-untyped-def]
        ack = super().push_facts(batch)
        self.chunks["ch_a"] = ChunkStatusView(chunk_id="ch_a", status=ChunkStatus.RUNNING, route_runner_id="r1")
        return ack

    def submit_completion(self, chunk_id, submission):  # type: ignore[no-untyped-def]
        response = super().submit_completion(chunk_id, submission)
        if chunk_id == "ch_b":
            self.chunks["ch_b"] = ChunkStatusView(
                chunk_id="ch_b",
                status=ChunkStatus.RUNNING,
                route_runner_id="r1",
                cost=ChunkUsageTotalView(
                    input_tokens=0,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_create_tokens=0,
                    cost_usd=7.0,
                    cost_partial=False,
                ),
            )
        return response


def test_tick_re_reads_a_chunk_once_after_its_own_write_lands(tmp_path):  # type: ignore[no-untyped-def]
    """Two of this tick's own writes, each invalidating the chunk it touched (D5) so a
    later reader in the SAME tick sees the reaction rather than the primed, stale view:
    (a) PULL's fact-batch flush reaching FILL's interrupted-claim reconciler, and (b) PULL's
    completion flush reaching OutboundDrain's own spend-cap re-check."""
    store = _store(tmp_path)

    # (a) ch_a: a binding-only, lease-less chunk (an interrupted claim) — primed as
    # unclaimed/READY, then a generic fact for it flushes through PULL before FILL reads it.
    store.record_binding(chunk_id="ch_a", environment_id="e_a", workdir="/ws/e_a", bound_at=_NOW)
    store.enqueue_outbound(kind=EVENT_RECORDED, chunk_id="ch_a", lease_id=None, payload="{}", created_at=_NOW)

    # (b) ch_b: an active lease with a buffered completion — primed under the spend cap,
    # then the flush's own hub reaction pushes it over, which _capped must catch fresh.
    _seed_active_lease(store, chunk="ch_b", lease="lease_b", pid=300, start="start-300")
    submission = CompletionSubmission(choice="pass", epoch=1, runner_id="r1", from_node_id="nd_build")
    store.enqueue_outbound(
        kind=COMPLETION_KIND,
        chunk_id="ch_b",
        lease_id="lease_b",
        payload=json.dumps({"submission": submission.model_dump(mode="json")}),
        created_at=_NOW,
    )

    hub = _WriteReactingHub()
    hub.queue = []
    hub.chunks["ch_a"] = ChunkStatusView(chunk_id="ch_a", status=ChunkStatus.READY, route_runner_id=None)
    hub.chunks["ch_b"] = ChunkStatusView(
        chunk_id="ch_b",
        status=ChunkStatus.RUNNING,
        route_runner_id="r1",
        cost=ChunkUsageTotalView(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=1.0,
            cost_partial=False,
        ),
    )
    hub.envelopes["ch_a"] = make_envelope("ch_a", "build", node_id="nd_build", choices=[("pass", "ok")])
    hub.apply_responses = [
        ApplyResponse(
            outcome=ApplyOutcome.NEXT,
            next_envelope=make_envelope("ch_b", "review", node_id="nd_review", choices=[("pass", "ok")]),
        )
    ]

    provider = FakeProvider({"e_a": "/ws/e_a"})
    probe = FakeProbe(alive={(300, "start-300")})
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness,
        probe=probe,
        config=LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, chunk_cap_usd=5.0),
    )

    tick(ctx)

    # ch_a was primed unclaimed/READY, then the fact flush's invalidation forced a fresh
    # read that saw RUNNING/ours: the interrupted-claim reconciler adopted it — a direct
    # spawn, never a re-claim (which a stale cache would have driven instead).
    assert hub.claims == []
    lease_a = store.active_lease_for_chunk("ch_a")
    assert lease_a is not None and lease_a.node_name == "build"

    # ch_b was primed at $1 (under the $5 cap), then the completion flush's own invalidation
    # forced _capped's re-check to see the post-write $7 total: escalated, never advanced
    # into the "review" node the hub's own apply-response offered.
    assert all(envelope.node.node_name != "review" for envelope, _ in harness.spawns)
    assert store.active_lease_for_chunk("ch_b") is None  # parked, not carried into "review"
    assert [f.kind for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED]


def test_no_module_under_runner_loop_imports_chunk_detail() -> None:
    """The migration's own acceptance criterion (blizzard#521): every one of the nine
    per-chunk reads now goes through ``IChunkViews``/``ChunkStatusView``, never the full
    ``ChunkDetail`` aggregate."""
    loop_dir = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "runner" / "loop"
    offenders = []
    for path in sorted(loop_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(alias.name == "ChunkDetail" for alias in node.names):
                offenders.append(str(path))
    assert offenders == []
