"""`Judgement`'s verdict launch, reached through `resume` — which standing record it sweeps, and every value it
opens the judge boundary with.

Unit tier over a real store: `tests/unit/runner/lifecycle/judgement/test_elicitation_launch_collect.py` proves the
launch/collect shape, and this pins what the launch *passes* — the standing record's sweep bound, the boundary's start, kind and generation."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.harness_set import (
    HarnessAssemblyKey,
    HarnessSet,
    HarnessSets,
    JudgeInvocation,
    WorkerInvocation,
)
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.session_settings import SessionEnvironment, SessionSettings
from blizzard.runner.leases.model import Lease, NewLease
from blizzard.runner.lifecycle.judgement.judgement import Judgement, KnownElicitation
from blizzard.runner.loop.context import LoopContext
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


@dataclass(frozen=True)
class _Rig:
    store: SqlAlchemyRunnerStore
    ctx: LoopContext
    lease: Lease


def _rig(tmp_path: Path) -> _Rig:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_mint(
        new_mint(
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
    return _Rig(store=store, ctx=ctx, lease=lease)


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
    judgement.resume()

    assert not os.path.exists(second)  # the relaunched attempt is swept; the fresh launch rewrites attempt 0


def test_the_judge_boundary_opens_at_the_tail_read_in_the_binding_workdir_and_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rig = _rig(tmp_path)
    asked: list[tuple[object, ...]] = []

    def resolve(
        self: LoopContext,
        session: SessionReference,
        workdir: str | None,
        workspace_id: str | None,
        *,
        lease_id: str | None = None,
    ) -> tuple[str | None, bool]:
        asked.append((session, workdir, workspace_id, lease_id))
        return "tok-1", True

    monkeypatch.setattr(type(rig.ctx), "resolve_boundary_start", resolve)

    judgement = Judgement.of(rig.ctx, rig.lease, generation=5, known_elicitation=KnownElicitation(standing=None))
    assert judgement is not None
    judgement.resume()

    assert asked == [(SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"), "/ws/e1", "ws1", "lease_1")]
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

    def resolve(
        self: LoopContext,
        session: SessionReference,
        workdir: str | None,
        workspace_id: str | None,
        *,
        lease_id: str | None = None,
    ) -> tuple[str | None, bool]:
        return None, False

    monkeypatch.setattr(type(rig.ctx), "resolve_boundary_start", resolve)
    current = rig.ctx.stores.liveness.lease_generation("lease_1")

    judgement = Judgement.of(rig.ctx, rig.lease)
    assert judgement is not None
    judgement.resume()

    (boundary,) = rig.store.open_boundaries_for_lease("lease_1")
    assert (boundary.generation, boundary.start_position, boundary.start_unreadable) == (current, None, False)
    assert current >= 1


def test_judge_launch_uses_worker_location_and_current_settings_then_retains_its_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = _rig(tmp_path)
    old = FakeHarness(handle=_HANDLE, verdict="pass")
    current = FakeHarness(handle=_HANDLE, verdict="fail")
    old_settings = SessionSettings(
        binary="old-binary",
        bundle_path="old-bundle",
        transcript_root="old-transcripts",
        config_dir="old-config",
        environment=SessionEnvironment((("HOME", "old-home"), ("PATH", "old-path")), "old-shell"),
    )
    new_settings = SessionSettings(
        binary="new-binary",
        bundle_path="new-bundle",
        transcript_root="new-transcripts",
        config_dir="new-config",
        environment=SessionEnvironment((("HOME", "new-home"), ("PATH", "new-path")), "new-shell"),
        permission_override="current-permission",
    )
    monkeypatch.setattr(old, "launch_settings", Mock(return_value=old_settings))
    monkeypatch.setattr(current, "launch_settings", Mock(return_value=new_settings))
    launch = Mock(wraps=old.judge)
    monkeypatch.setattr(old, "judge", launch)
    usable = Mock(wraps=old.has_usable_output)
    monkeypatch.setattr(old, "has_usable_output", usable)
    old_registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=old)})
    new_registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=current)})
    first = Mock(spec=HarnessSet, registry=old_registry, versions=None)
    second = Mock(spec=HarnessSet, registry=new_registry, versions=None)
    sets = HarnessSets(Mock(side_effect=[first, second]))
    sets.select(HarnessAssemblyKey((1,)))
    generation = rig.store.lease_generation("lease_1")
    worker = WorkerInvocation("lease_1", generation)
    sets.retain(worker)
    sets.select(HarnessAssemblyKey((2,)))
    ctx = replace(rig.ctx, harness_sets=sets)
    judgement = Judgement.of(ctx, rig.lease)
    assert judgement is not None

    judgement.resume()

    settings = launch.call_args.kwargs["session_settings"]
    assert (settings.binary, settings.bundle_path, settings.transcript_root, settings.config_dir) == (
        "old-binary",
        "old-bundle",
        "old-transcripts",
        "old-config",
    )
    assert settings.environment.variables == {"HOME": "old-home", "PATH": "new-path"}
    assert (settings.environment.shell, settings.permission_override) == ("new-shell", "current-permission")
    assert sets.find(JudgeInvocation("lease_1", generation)) is first
    sets.release(worker)
    elicitation = rig.store.in_flight_elicitation("lease_1", 1)
    assert elicitation is not None
    judgement.collect(elicitation)
    usable.assert_called_once()
