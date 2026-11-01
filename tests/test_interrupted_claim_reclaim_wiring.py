"""`InterruptedClaims.reclaim` — every argument it hands the hub, the route-token store and the spawn.

Unit tier over a real store, with a recording spy at the spawn seam and a recording workspace-reach stub: the
loop scenarios prove a reclaim happens, and this pins what the claim and the spawn are *given*."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.claim import InterruptedClaims
from blizzard.runner.lifecycle.spawn import Spawner
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
_OWNER = "prior-owner"


class _Workspaces:
    """Delegates to the real workspaces, answering `held` with a fixed workspace and recording what it was asked."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.held_calls: list[tuple[Any, Any]] = []

    def held(self, workspace_id: Any, bound: Any) -> str:
        self.held_calls.append((workspace_id, bound))
        return "ws-held"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    # A prior lease for the chunk, so the recovery owner is one the spawn must carry.
    store.record_lease(
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
    store.record_spawn(
        "lease_0",
        pid=1,
        process_start_time="t",
        session=SessionReference(_OWNER, "sess-0"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws-bound")
    store.record_binding(chunk_id="ch_1", environment_id="e2", workdir="/ws/e2", bound_at=_NOW, workspace_id="ws-bound")
    hub = FakeHub()
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    hub.claim_outcome = claimed_outcome("ch_1", envelope, route_token="rtok_new")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )
    workspaces = _Workspaces(ctx.workspaces)
    ctx = replace(ctx, workspaces=workspaces)
    spawns: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(Spawner, "spawn", lambda self, *a, **k: spawns.append((a, k)))
    return SimpleNamespace(
        store=store,
        hub=hub,
        ctx=ctx,
        envelope=envelope,
        workspaces=workspaces,
        spawns=spawns,
        bindings=store.bindings_for_chunk("ch_1"),
    )


def test_the_claim_names_the_chunk_the_held_workspace_and_every_held_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path, monkeypatch)

    InterruptedClaims(rig.ctx).reclaim("ch_1", rig.bindings)

    assert rig.workspaces.held_calls == [("ws-bound", rig.ctx.config.bound_workspace_ids)]
    assert len(rig.hub.claims) == 1
    claim = rig.hub.claims[0]
    assert (claim.chunk_id, claim.workspace_id) == ("ch_1", "ws-held")
    assert claim.environment_ids == ["e1", "e2"]


def test_a_won_reclaim_spawns_the_claimed_envelope_into_the_held_environments_under_the_prior_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path, monkeypatch)

    with capture_logs() as logs:
        InterruptedClaims(rig.ctx).reclaim("ch_1", rig.bindings)

    assert rig.spawns == [
        (
            ("ch_1", rig.envelope, [AcquiredEnvironment("e1", "/ws/e1"), AcquiredEnvironment("e2", "/ws/e2")]),
            {"via": "reclaim", "harness_id": _OWNER},
        )
    ]
    assert rig.ctx.stores.tokens.route_token("ch_1") == "rtok_new"
    assert [(entry["event"], entry["chunk_id"]) for entry in logs if entry["event"].startswith("re-claimed")] == [
        ("re-claimed interrupted chunk — spawning current node", "ch_1")
    ]
