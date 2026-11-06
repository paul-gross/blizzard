"""Pinning tests for runner-loop decisions that were defended only by prose.

Each test here fences one decision a long comment used to argue for: the comment now
names the test, and reverting the decision fails the named assertion below.
Driven against a real (tmp sqlite) runner store with the seam fakes, per the tier rules.
"""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.runner.harness.adapter import ResumeHandle, WorkerHandle, WorkerPreamble
from blizzard.runner.loop.steps import Advance
from blizzard.runner.node_steps.submissions import ApplyReply
from tests.support.pin_runner_loop import (
    _CHOICES,  # pyright: ignore[reportPrivateUsage]
    _HANDLE,  # pyright: ignore[reportPrivateUsage]
    _NOW,  # pyright: ignore[reportPrivateUsage]
    _seed_running_lease,  # pyright: ignore[reportPrivateUsage]
    _store,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
)


class _RecordingResumeHarness(FakeHarness):
    """Captures whether the resume-fired fact was already durable at the resume call."""

    def __init__(
        self,
        *,
        store: SqlAlchemyRunnerStore,
        lease_id: str,
        epoch: int,
        handle: WorkerHandle,
        verdict: str | None,
        assessment: str = "",
    ) -> None:
        super().__init__(handle=handle, verdict=verdict, assessment=assessment)
        self._store = store
        self._lease_id = lease_id
        self._epoch = epoch
        self.fired_at_resume: list[bool] = []

    def resume_with_message(
        self,
        session_cwd: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        model: str | None = None,
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> ResumeHandle:
        self.fired_at_resume.append(self._store.nudge_fired(self._lease_id, self._epoch))
        return super().resume_with_message(
            session_cwd,
            session_id,
            message,
            stdout_path,
            preamble=preamble,
            chunk_id=chunk_id,
            model=model,
            effort=effort,
            compaction_window=compaction_window,
        )


def test_the_resume_guard_fact_is_durable_before_the_resume_runs(tmp_path: Path) -> None:
    """ "At most one resume per (lease, epoch)" is structural only if the guard fact is
    written first: recorded after the resume, a crash in between leaves recovery unable to
    tell "resumed, worker ignored it" from "never resumed"."""
    store = _store(tmp_path)
    _seed_running_lease(store, lease="lease_r", node_id="nd_review")
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.DONE)]
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
