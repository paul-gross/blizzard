"""Pinning tests for runner-loop decisions that were defended only by prose (issue #270).

Each test here fences one decision a long comment used to argue for: the comment now
names the test, and reverting the decision fails the named assertion below.
Driven against a real (tmp sqlite) runner store with the seam fakes, per the tier rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle, WorkerPreamble
from blizzard.runner.loop.build import LoopWiring
from blizzard.runner.loop.checks import DEFAULT_CHECK_TIMEOUT, CheckOutcome
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.judgement import Judgement
from blizzard.runner.loop.steps import Advance, Resume
from blizzard.runner.loop.tick import tick
from blizzard.wire.chunk import ChunkDetail, PauseView, RouteView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeCheckRunner,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(store, *, lease="lease_1", chunk="ch_1", node_id="nd_build", epoch=1):  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id=node_id,
            node_name="build",
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease, pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


# RESUME keys on the pause FACT, never the derived status (issue #46).
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_resume_parks_a_paused_chunk_whose_derived_status_hides_the_pause(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A chunk both paused and parked on a question derives ``waiting_on_human``, so the
    ``pause`` view is the only witness that it is paused. RESUME must park it and keep the
    claim; a status-keyed branch would read "the world moved on" and abandon it."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_resume_intent(lease_id="lease_1", marked_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.WAITING_ON_HUMAN,  # the lossy read — paused AND asked
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
        pause=PauseView(by="operator", set_at="2026-08-04T12:00:00Z"),
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


# The checks re-run marker is keyed (lease, epoch) — never (chunk, node) (issue #114).
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_checks_rerun_under_a_fresh_lease_epoch_at_the_same_chunk_and_node(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    bindings = store.bindings_for_chunk("ch_1")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, checks=["mise run lint"])
    red = FakeCheckRunner({"mise run lint": CheckOutcome(passed=False, output_tail="boom")})
    green = FakeCheckRunner({"mise run lint": CheckOutcome(passed=True, output_tail="ok")})

    def _ctx(check_runner):  # type: ignore[no-untyped-def]
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


# The nudge-fired guard fact lands BEFORE the resume it guards runs (issues #113, #422).
# --------------------------------------------------------------------------- #


class _RecordingResumeHarness(FakeHarness):
    """Captures whether the resume-fired fact was already durable at the resume call."""

    def __init__(self, *, store, lease_id: str, epoch: int, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._store = store
        self._lease_id = lease_id
        self._epoch = epoch
        self.fired_at_resume: list[bool] = []

    def resume_with_message(
        self,
        workdir: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> int:
        self.fired_at_resume.append(self._store.nudge_fired(self._lease_id, self._epoch))
        return super().resume_with_message(
            workdir,
            session_id,
            message,
            stdout_path,
            preamble=preamble,
            chunk_id=chunk_id,
            effort=effort,
            compaction_window=compaction_window,
        )


@pytest.mark.component
def test_the_resume_guard_fact_is_durable_before_the_resume_runs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """ "At most one resume per (lease, epoch)" is structural only if the guard fact is
    written first: recorded after the resume, a crash in between leaves recovery unable to
    tell "resumed, worker ignored it" from "never resumed"."""
    store = _store(tmp_path)
    _seed_running_lease(store, lease="lease_r", node_id="nd_review")
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = _RecordingResumeHarness(
        store=store,
        lease_id="lease_r",
        epoch=1,
        handle=_HANDLE,
        verdict="fail",
        assessment="the shared assessment",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),  # the worker exited
        worktree_git=FakeWorktreeGit(),
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()

    assert harness.judged == [], "no verdict should be elicited on a resumed premature exit"
    assert harness.fired_at_resume == [True], "the resume ran before its guard fact was durable"


# The prompts are resolved by the caller and injected, never re-derived here.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_loop_wiring_uses_the_injected_prompts_and_never_re_derives_them(tmp_path: Path) -> None:
    """Re-deriving them here would raise ``ConfigError`` on a configured-but-missing prompt
    file from whatever thread this runs on — for the daemon, the background loop thread —
    instead of from ``host``'s own startup call."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        runner_prompt_file="does-not-exist.md",
        workspace_prompt_file="also-missing.md",
    )

    try:
        ctx = LoopWiring(config, "ws prose", "runner prose").context(FakeHub())
    except ConfigError as exc:  # pragma: no cover - the pinned regression
        pytest.fail(f"the prompts were re-derived here instead of taken from the caller: {exc}")

    assert (ctx.config.workspace_prompt, ctx.config.runner_prompt) == ("ws prose", "runner prose")


# The external-subscription-usage sample is the tick's LAST step (issue #218).
# --------------------------------------------------------------------------- #


class _ClaimObservingHarness(FakeHarness):
    """Records how many route claims the hub had taken by the time the sampler ran."""

    def __init__(self, *, hub: FakeHub, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self._hub = hub
        self.claims_at_sample: list[int] = []

    def sample_external_subscription_usage(self):  # type: ignore[no-untyped-def]
        self.claims_at_sample.append(len(self._hub.claims))
        return super().sample_external_subscription_usage()


@pytest.mark.unit
def test_the_external_usage_sample_runs_after_fill_has_claimed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Its network call gates nothing any step reads, so it must never sit ahead of the
    steps that reap and spawn and delay them on a diagnostic read."""
    store = _store(tmp_path)
    hub = FakeHub()
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", envelope)
    harness = _ClaimObservingHarness(hub=hub, handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(alive={(_HANDLE.pid, _HANDLE.process_start_time)}),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, external_usage_sample_interval_seconds=300),
    )

    tick(ctx)

    assert len(hub.claims) == 1  # FILL did claim this tick
    assert harness.claims_at_sample == [1], "the sampler ran ahead of FILL's claim — it must be the tick's last step"
