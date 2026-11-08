"""Interrupted-claim recovery keeps its carried owner only while the deciding envelope admits it.

Unit tier over a real store and the real `Spawner`, with a fake default harness: each of the three fresh-recovery
callers (`adopt`, `_resume_requeued`, `reclaim`) either spawns under the prior owner or escalates
`no-acceptable-harness` with no worker started."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.claim import InterruptedClaims
from blizzard.runner.lifecycle.spawn import Spawner
from blizzard.runner.node_steps.chunk_state import ChunkState
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_OWNER = CLAUDE_CODE_HARNESS_ID


def _rig(tmp_path: Path, *, harnesses: list[str] | None, prior_owner: str | None = _OWNER):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    if prior_owner is not None:
        store.record_mint(
            new_mint(
                NewLease(
                    lease_id="lease_0",
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    node_id="nd_build",
                    node_name="build",
                    epoch=1,
                    retries_max=2,
                    created_at=_NOW,
                )
            )
        )
        store.record_mint_owner("lease_0", prior_owner)
        store.record_spawn(
            "lease_0", pid=1, process_start_time="t", session=SessionReference(prior_owner, "sess-0"), spawned_at=_NOW
        )
        store.record_closure(lease_id="lease_0", chunk_id="ch_1", node_id="nd_build", reason="failed", closed_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    store.set_route_token("ch_1", token="rtok", at=_NOW)
    hub = FakeHub()
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], session_harnesses=harnesses)
    hub.envelopes["ch_1"] = envelope
    hub.claim_outcome = claimed_outcome("ch_1", envelope, route_token="rtok_new")
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )
    return SimpleNamespace(store=store, hub=hub, ctx=ctx, harness=harness, bindings=store.bindings_for_chunk("ch_1"))


def _recover(rig: Any, caller: str) -> None:
    claims = InterruptedClaims(rig.ctx)
    if caller == "adopt":
        view = ChunkState(chunk_id="ch_1", status=ChunkStatus.RUNNING, route_runner_id="rn", latest_epoch=1)
        claims.adopt("ch_1", view)
    elif caller == "requeue-resume":
        claims._resume_requeued("ch_1", 1)  # pyright: ignore[reportPrivateUsage]
    else:
        claims.reclaim("ch_1", rig.bindings)


def _escalations(rig: Any) -> list[dict[str, Any]]:
    events = [b for b in rig.store.pending_outbound() if b.kind == EVENT_RECORDED and b.chunk_id == "ch_1"]
    return [json.loads(e.payload) for e in events]


_CALLERS = ["adopt", "requeue-resume", "reclaim"]


@pytest.mark.parametrize("caller", _CALLERS)
@pytest.mark.parametrize("harnesses", [None, [], [_OWNER], ["other", _OWNER]])
def test_an_admitted_or_unrestricted_prior_owner_is_kept(tmp_path: Path, caller: str, harnesses: list[str] | None):
    rig = _rig(tmp_path, harnesses=harnesses)

    _recover(rig, caller)

    assert len(rig.harness.spawns) == 1
    assert _escalations(rig) == []


@pytest.mark.parametrize("caller", _CALLERS)
def test_an_excluded_prior_owner_escalates_without_a_spawn_or_a_substitute(tmp_path: Path, caller: str):
    rig = _rig(tmp_path, harnesses=["other"])

    _recover(rig, caller)

    assert rig.harness.spawns == []
    assert [e for e in rig.store.open_escalations() if e.chunk_id == "ch_1"] != []
    payloads = _escalations(rig)
    assert len(payloads) == 1
    assert payloads[0]["kind"] == "no-acceptable-harness"
    assert payloads[0]["detail"] == {
        "via": "reclaim" if caller == "reclaim" else caller,
        "attempted": ["other"],
        "skipped": [{"harness_id": _OWNER, "reason": "not-a-member"}],
    }


@pytest.mark.parametrize("caller", _CALLERS)
def test_a_repeat_recovery_adds_no_second_escalation_and_no_spawn(tmp_path: Path, caller: str):
    rig = _rig(tmp_path, harnesses=["other"])

    _recover(rig, caller)
    _recover(rig, caller)

    assert rig.harness.spawns == []
    assert len(_escalations(rig)) == 1


@pytest.mark.parametrize("caller", _CALLERS)
def test_no_prior_owner_takes_the_ordinary_selection_path(tmp_path: Path, caller: str):
    rig = _rig(tmp_path, harnesses=None, prior_owner=None)

    _recover(rig, caller)

    assert len(rig.harness.spawns) == 1
    assert _escalations(rig) == []


def test_plain_spawn_stays_unguarded(tmp_path: Path):
    rig = _rig(tmp_path, harnesses=["other"])

    Spawner(rig.ctx).spawn("ch_1", rig.hub.envelopes["ch_1"], [], via="adopt", harness_id=_OWNER)

    assert _escalations(rig) == []
