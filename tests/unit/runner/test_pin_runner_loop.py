"""Pinning tests for runner-loop decisions that were defended only by prose.

Each test here fences one decision a long comment used to argue for: the comment now
names the test, and reverting the decision fails the named assertion below.
Driven against a real (tmp sqlite) runner store with the seam fakes, per the tier rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.judgement.check_runner import DEFAULT_CHECK_TIMEOUT, CheckOutcome
from blizzard.runner.lifecycle.judgement.judgement import Judgement
from blizzard.runner.loop.context import DEFAULT_RETRIES_MAX, LoopContext
from blizzard.runner.loop.steps import Resume
from blizzard.runner.node_steps.chunk_state import ChunkPause, ChunkState
from tests.support.host_files import one_workspace
from tests.support.pin_runner_loop import (
    _CHOICES,  # pyright: ignore[reportPrivateUsage]
    _HANDLE,  # pyright: ignore[reportPrivateUsage]
    _NOW,  # pyright: ignore[reportPrivateUsage]
    _seed_running_lease,  # pyright: ignore[reportPrivateUsage]
    _store,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.runner_fakes import (
    FakeCheckRunner,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    loop_context,
    make_context,
    make_envelope,
    migrated_store_at,
)


def test_default_retries_max_is_two() -> None:
    """``nodes.md``: an omitted `retries:` resolves to two retries specifically."""
    assert DEFAULT_RETRIES_MAX == 2


def test_resume_parks_a_paused_chunk_whose_derived_status_hides_the_pause(tmp_path: Path) -> None:
    """A chunk both paused and parked on a question derives ``waiting_on_human``, so the
    ``pause`` view is the only witness that it is paused. RESUME must park it and keep the
    claim; a status-keyed branch would read "the world moved on" and abandon it."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_resume_intent(lease_id="lease_1", marked_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkState(
        chunk_id="ch_1",
        status=ChunkStatus.WAITING_ON_HUMAN,  # the lossy read — paused AND asked
        latest_epoch=1,
        route_runner_id="r1",
        pause=ChunkPause(by="operator", set_at="2026-08-04T12:00:00Z"),
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    Resume(ctx).run()

    assert store.pause_parked_lease_ids() == {"lease_1"}, "the pause was read off the status, not the fact"
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.epoch == 1  # the claim is kept, not abandoned
    assert store.held_environment_ids() == ["e1"]
    assert provider.released == []


def test_checks_rerun_under_a_fresh_lease_epoch_at_the_same_chunk_and_node(tmp_path: Path) -> None:
    """A retry mints a fresh ``(lease, epoch)`` at the same ``(chunk, node)``, so the
    marker must be unset and the checks must run again against the rebuilt tree."""
    store = _store(tmp_path)
    _seed_running_lease(store, lease="lease_a", epoch=1)
    store.record_lease(
        NewLease(
            lease_id="lease_b",  # the retry: same chunk, same node, fresh lease + epoch
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=2,
            retries_max=2,
            created_at=_NOW,
        )
    )
    bindings = store.bindings_for_chunk("ch_1")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run lint"])
    red = FakeCheckRunner({"mise run lint": CheckOutcome(passed=False, output_tail="boom")})
    green = FakeCheckRunner({"mise run lint": CheckOutcome(passed=True, output_tail="ok")})

    def _ctx(check_runner: FakeCheckRunner) -> LoopContext:
        return make_context(
            store,
            hub=FakeHub(),
            provider=FakeProvider({"e1": "/ws/e1"}),
            harness=FakeHarness(handle=_HANDLE, verdict="pass"),
            probe=FakeProbe(),
            check_runner=check_runner,
            clock=FixedClock(_NOW),
        )

    first = store.lease("lease_a")
    second = store.lease("lease_b")
    assert first is not None and second is not None

    assert [(r.command, r.passed) for r in Judgement(_ctx(red), first, envelope, bindings).checks()] == [
        ("mise run lint", False)
    ]

    results = Judgement(_ctx(green), second, envelope, bindings).checks()

    assert green.calls == [("mise run lint", "/ws/e1", DEFAULT_CHECK_TIMEOUT)], (
        "the retry read a prior attempt's recorded result back instead of re-running its checks"
    )
    assert [(r.command, r.passed) for r in results] == [("mise run lint", True)]


def test_loop_wiring_uses_the_injected_runner_prompt_and_never_re_derives_it(tmp_path: Path) -> None:
    """Re-deriving it here would raise ``ConfigError`` on a configured-but-missing prompt
    file from whatever thread this runs on — for the daemon, the background loop thread —
    instead of from ``host``'s own startup call."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=f"sqlite:///{tmp_path / 'runner.db'}",
        workspaces=one_workspace(root=str(tmp_path / "workspace")),
        runner_prompt_file="does-not-exist.md",
    )
    migrated_store_at(config.db_url)  # the graph reads the identity row at boot, as `host` does once current

    try:
        with loop_context(config, runner_prompt="runner prose") as ctx:
            prompt = ctx.config.runner_prompt
    except ConfigError as exc:  # pragma: no cover - the pinned regression
        pytest.fail(f"the runner prompt was re-derived here instead of taken from the caller: {exc}")

    assert prompt == "runner prose"
