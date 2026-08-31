"""ADVANCE resumes a premature exit instead of judging it (issues #113, #143, #422).

A real ADVANCE tick against a tmp store and virtual clock, proving a step exiting with a
required `produces:` name unattached is resumed, not judged, and that "at most one resume
per (lease, epoch)" holds."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle, WorkerPreamble
from blizzard.runner.loop.produces import ProducesReconciler
from blizzard.runner.loop.steps import Advance, Pull
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.graph import ProducesEntry
from tests.runner_fakes import (
    FakeCheckRunner,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


class _AttachingOnResumeHarness(FakeHarness):
    """A :class:`FakeHarness` whose ``resume_with_message`` attaches on the worker's
    behalf, standing in for a worker that runs ``blizzard runner attach`` in response to
    the wake it was resumed with."""

    def __init__(
        self, *, store, clock, lease_id: str, chunk_id: str, node_id: str, epoch: int, name: str, content: str, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._store = store
        self._clock = clock
        self._lease_id = lease_id
        self._chunk_id = chunk_id
        self._node_id = node_id
        self._epoch = epoch
        self._name = name
        self._content = content

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
        pid = super().resume_with_message(
            workdir,
            session_id,
            message,
            stdout_path,
            preamble=preamble,
            chunk_id=chunk_id,
            effort=effort,
            compaction_window=compaction_window,
        )
        self._store.record_attachment(
            lease_id=self._lease_id,
            chunk_id=self._chunk_id,
            node_id=self._node_id,
            epoch=self._epoch,
            name=self._name,
            content=self._content,
            attached_at=self._clock.now(),
        )
        return pid


class _DeclaringGitCommitOnResumeHarness(FakeHarness):
    """A :class:`FakeHarness` whose ``resume_with_message`` declares a git commit on the
    worker's behalf, standing in for a worker that pushes and declares in response to the
    wake (issue #143 re-review)."""

    def __init__(
        self,
        *,
        store,
        clock,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        repo: str,
        environment_id: str,
        branch: str,
        commit: str,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._store = store
        self._clock = clock
        self._lease_id = lease_id
        self._chunk_id = chunk_id
        self._node_id = node_id
        self._epoch = epoch
        self._repo = repo
        self._environment_id = environment_id
        self._branch = branch
        self._commit = commit

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
        pid = super().resume_with_message(
            workdir,
            session_id,
            message,
            stdout_path,
            preamble=preamble,
            chunk_id=chunk_id,
            effort=effort,
            compaction_window=compaction_window,
        )
        self._store.record_git_commit_declaration(
            lease_id=self._lease_id,
            chunk_id=self._chunk_id,
            node_id=self._node_id,
            epoch=self._epoch,
            environment_id=self._environment_id,
            repo=self._repo,
            branch=self._branch,
            commit=self._commit,
            declared_at=self._clock.now(),
        )
        return pid


def _seed_exited_lease(store, *, lease_id: str, chunk_id: str, node_id: str, epoch: int) -> None:
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id=node_id,
            node_name="review",
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease_id, pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id=chunk_id, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


@pytest.mark.component
def test_unmet_produces_exit_resumes_instead_of_being_judged(tmp_path: Path) -> None:
    """Reconstructs the observed premature-exit shape (blizzard#422): a clean exit with an
    unattached required `produces:` name is resumed, not judged — no verdict elicited, no
    attempt failed, no `checks:` run, retry count and epoch untouched."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_review", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"], checks=["pytest -m unit"]
    )
    check_runner = FakeCheckRunner()
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"),
        # The observed shape: the model ended its turn with no verdict ever elicited.
        verdict=None,
        assessment="",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
        check_runner=check_runner,
        clock=clock,
    )

    Advance(ctx).run()

    assert harness.judged == [], "no verdict should be elicited on a resumed premature exit"
    assert len(harness.resumed) == 1, "the dormant session should be resumed exactly once"
    assert check_runner.calls == [], "checks must not run on an exit that is resumed, not judged"
    assert hub.completions == [], "nothing was judged, so nothing was buffered"
    assert store.nudge_fired("lease_r", 1) is True

    active = store.list_active_leases()
    assert len(active) == 1
    assert active[0].lease_id == "lease_r"
    assert active[0].epoch == 1, "no epoch bump — this is a resume, not a retry"
    assert active[0].session_id == "sess-a", "the same session, re-attached"

    _, _, resume_message = harness.resumed[0]
    assert "review-findings" in resume_message
    assert "blizzard runner artifact create --name review-findings" in resume_message
    # The resume must carry the per-lease identity — a preamble with a re-minted token —
    # or the attach it elicits cannot authenticate.
    preamble, chunk_id = harness.resumed_identity[0]
    assert preamble is not None and preamble.lease_id == "lease_r" and preamble.lease_token
    assert chunk_id == "ch_1"


@pytest.mark.component
def test_unmet_produces_resume_picks_up_the_attach_on_the_next_exit(tmp_path: Path) -> None:
    """A worker that attaches in response to the resume, then exits again, is judged
    normally on that second exit — completion assembly prefers the attached content over
    the assessment fallback."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_review", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = _AttachingOnResumeHarness(
        store=store,
        clock=clock,
        lease_id="lease_r",
        chunk_id="ch_1",
        node_id="nd_review",
        epoch=1,
        name="review-findings",
        content="attached after the resume",
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"),
        verdict="fail",
        assessment="the shared assessment",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
        clock=clock,
    )

    Advance(ctx).run()  # first exit: unmet produces — resumed, not judged
    Advance(ctx).run()  # second exit: the attach landed — launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    assert len(harness.resumed) == 1, "exactly one resume — the attach closed the gap"
    assert len(harness.judged) == 1, "exactly one verdict elicited, on the second exit"

    _, submission = hub.completions[0]
    by_name = {a.name: a for a in submission.artifacts}
    assert by_name["review-findings"].kind is ArtifactKind.ASSET
    assert by_name["review-findings"].content == "attached after the resume"
    assert by_name["review-findings"].attached is True


@pytest.mark.component
def test_unmet_produces_resume_picks_up_a_git_commit_declared_on_the_next_exit(tmp_path: Path) -> None:
    """The mirror of the asset-only test above, but for a `git_commit` spec: a worker's
    declaration in response to the resume must fold into the next exit's completion."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_build", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=_CHOICES,
        produces=[ProducesEntry(name="commit", kind=ArtifactKind.GIT_COMMIT)],
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = _DeclaringGitCommitOnResumeHarness(
        store=store,
        clock=clock,
        lease_id="lease_r",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        repo="toy-api",
        environment_id="e1",
        branch="feature/worker-declared",
        commit="deadbeef",
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"),
        verdict="fail",
        assessment="the shared assessment",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
        clock=clock,
    )

    Advance(ctx).run()  # first exit: unmet produces — resumed, not judged
    Advance(ctx).run()  # second exit: the declaration landed — launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    assert len(harness.resumed) == 1
    assert len(harness.judged) == 1
    _, _, resume_message = harness.resumed[0]
    assert "commit" in resume_message and "git_commit" in resume_message
    assert "blizzard runner artifact commit" in resume_message

    _, submission = hub.completions[0]
    git_artifacts = [a for a in submission.artifacts if a.kind is ArtifactKind.GIT_COMMIT]
    assert len(git_artifacts) == 1, "the commit declared after the resume never reached the completion"
    assert git_artifacts[0].repo == "toy-api"
    assert git_artifacts[0].branch_name == "feature/worker-declared"
    assert git_artifacts[0].commit_hash == "deadbeef"
    # The `git_commit`-kind spec is satisfied by kind, never asset-collected by its own
    # spec name — no phantom `commit` ASSET should ride alongside it.
    assert [a for a in submission.artifacts if a.name == "commit"] == []
    assert not any(a.kind is ArtifactKind.ASSET for a in submission.artifacts)


@pytest.mark.component
def test_second_premature_exit_under_the_same_lease_and_epoch_falls_through_to_judge(tmp_path: Path) -> None:
    """Past the one-resume cap, a still-stuck worker terminates: the second exit under the
    same `(lease, epoch)` with `produces:` still unmet is judged with the assessment
    fallback, rather than resumed a second time."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_review", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"),
        verdict="fail",
        assessment="the shared assessment",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
        clock=clock,
    )

    Advance(ctx).run()  # first exit: resumed, cap spent
    Advance(ctx).run()  # second exit: still unmet, but the cap is spent — launches
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    assert len(harness.resumed) == 1, "the cap must not allow a second resume"
    assert len(harness.judged) == 1, "the second exit must fall through to a real verdict"

    _, submission = hub.completions[0]
    by_name = {a.name: a for a in submission.artifacts}
    assert by_name["review-findings"].content == "the shared assessment"
    assert by_name["review-findings"].attached is False


@pytest.mark.component
def test_resume_fact_already_set_skips_a_second_resume_attempt(tmp_path: Path) -> None:
    """The durable fact is honored across recovery too — the shape a crash between the
    fired-fact write and the resume leaves behind — resuming no second time and converging
    straight to a judged completion with the assessment fallback."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_review", epoch=1)
    # Simulate recovery from a crash at `nudge.after-fired-fact.before-resume`: the fact
    # is durable, the resume it guards may or may not have actually run.
    store.record_nudge_fired(lease_id="lease_r", epoch=1, at=_NOW)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"),
        verdict="fail",
        assessment="the shared assessment",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
        clock=clock,
    )

    lease = store.list_active_leases()[0]
    Advance(ctx)._advance_exited_worker(lease)  # launches the detached elicitation
    Advance(ctx)._advance_exited_worker(lease)  # collects it — the fake pid reads dead by default
    Pull(ctx).run()  # the flusher delivers the buffered completion to the hub

    assert harness.resumed == [], "the fact already being set must suppress any resume attempt"
    assert len(harness.judged) == 1, "the exit must still be judged, not silently dropped"

    _, submission = hub.completions[0]
    by_name = {a.name: a for a in submission.artifacts}
    assert by_name["review-findings"].content == "the shared assessment"
    assert by_name["review-findings"].attached is False


@pytest.mark.component
def test_fully_attached_node_does_not_resume(tmp_path: Path) -> None:
    """A node whose every `produces` name is already attached (or git-covered) elicits no
    resume at all — the wake is a spawn primitive, not spent when nothing is missing."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_review", epoch=1)
    store.record_attachment(
        lease_id="lease_r",
        chunk_id="ch_1",
        node_id="nd_review",
        epoch=1,
        name="review-findings",
        content="already attached before judgement",
        attached_at=_NOW,
    )

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"),
        verdict="pass",
        assessment="the shared assessment",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
        clock=clock,
    )

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()

    assert harness.resumed == [], "a fully-attached node must not be resumed"
    assert len(harness.judged) == 1
    assert store.nudge_fired("lease_r", 1) is False

    _, submission = hub.completions[0]
    by_name = {a.name: a for a in submission.artifacts}
    assert by_name["review-findings"].content == "already attached before judgement"
    assert by_name["review-findings"].attached is True


@pytest.mark.unit
def test_nudge_message_branches_on_kind_and_stays_harness_inert() -> None:
    """`ProducesReconciler.nudge_message` (issue #143) names the kind-appropriate declaration verb
    per unmet spec, never the deprecated `attach` alias, and every rendered line is
    `#`-prefixed so the mock harness's prompt-is-program `exec` still sees a legal no-op."""
    missing = [
        ProducesEntry(name="review-findings", kind=ArtifactKind.ASSET),
        ProducesEntry(name="commit", kind=ArtifactKind.GIT_COMMIT),
    ]

    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    message = ProducesReconciler(envelope).nudge_message(missing)

    for line in message.splitlines():
        assert line.startswith("#"), f"non-inert line in the nudge message: {line!r}"
    assert "artifact create --name review-findings" in message
    assert "artifact commit --repo <repo> --branch <branch> --commit <sha>" in message
    assert "`<repo>` is its name in the environment's manifest" in message
    assert "runner attach" not in message
