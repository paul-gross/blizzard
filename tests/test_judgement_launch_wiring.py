"""`Judgement._launch` — which standing record it sweeps, and every value it opens the judge boundary with.

Unit tier over a real store: `tests/test_elicitation_launch_collect.py` proves the launch/collect shape, and this pins
what the launch *passes* — the standing record's sweep bound, the boundary's start, kind and generation."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.judgement.judgement import Judgement, KnownElicitation
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

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


def _rig(tmp_path: Path):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
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
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok"), ("fail", "no")])
    hub = FakeHub()
    hub.envelopes["ch_1"] = envelope
    hub.claim_outcome = claimed_outcome("ch_1", envelope)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )
    lease = store.active_lease("lease_1")
    assert lease is not None
    return SimpleNamespace(store=store, ctx=ctx, lease=lease)


def test_a_standing_record_the_caller_read_is_swept_through_its_relaunch_count(tmp_path: Path):
    rig = _rig(tmp_path)
    files = rig.ctx.elicitation_files
    first, second = files.output_path("lease_1", 1, attempt=0), files.output_path("lease_1", 1, attempt=1)
    rig.store.record_elicitation_launch("lease_1", 1, output_path=first, at=_NOW)
    rig.store.record_elicitation_relaunch("lease_1", 1, output_path=second)
    standing = rig.store.in_flight_elicitation("lease_1", 1)
    assert standing is not None and standing.relaunch_count == 1
    for path in (first, second):
        with open(path, "w", encoding="utf-8") as f:
            f.write("stale")

    judgement = Judgement.of(rig.ctx, rig.lease, known_elicitation=KnownElicitation(standing=standing))
    assert judgement is not None
    judgement._launch()

    assert not os.path.exists(second)  # the relaunched attempt is swept; the fresh launch rewrites attempt 0


def test_the_judge_boundary_opens_at_the_tail_read_in_the_binding_workdir_and_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    asked: list[tuple[object, ...]] = []

    def resolve(self, session, workdir, workspace_id):
        asked.append((session, workdir, workspace_id))
        return "tok-1", True

    monkeypatch.setattr(type(rig.ctx), "resolve_boundary_start", resolve)

    judgement = Judgement.of(rig.ctx, rig.lease, generation=5, known_elicitation=KnownElicitation(standing=None))
    assert judgement is not None
    judgement._launch()

    assert asked == [(SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"), "/ws/e1", "ws1")]
    (boundary,) = rig.store.open_boundaries_for_lease("lease_1")
    assert (boundary.kind, boundary.generation, boundary.start_position, boundary.start_unreadable) == (
        "judge",
        5,
        "tok-1",
        True,
    )
    assert (boundary.lease_id, boundary.chunk_id, boundary.node_id, boundary.epoch, boundary.opened_at) == (
        "lease_1",
        "ch_1",
        "nd_build",
        1,
        _NOW,
    )


def test_a_launch_given_no_generation_opens_the_boundary_under_the_leases_current_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    monkeypatch.setattr(
        type(rig.ctx), "resolve_boundary_start", lambda self, session, workdir, workspace_id: (None, False)
    )
    current = rig.ctx.stores.liveness.lease_generation("lease_1")

    judgement = Judgement.of(rig.ctx, rig.lease)
    assert judgement is not None
    judgement._launch()

    (boundary,) = rig.store.open_boundaries_for_lease("lease_1")
    assert (boundary.generation, boundary.start_position, boundary.start_unreadable) == (current, None, False)
    assert current >= 1
