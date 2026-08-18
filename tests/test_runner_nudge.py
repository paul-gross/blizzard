"""ADVANCE's produces-unmet nudge-once (issue #113, Phase 4).

A real ADVANCE tick against a real tmp store and virtual clock, proving criterion 5
(nudge fires once, lists missing names, before submission) and the durable "at most one
nudge per (lease, epoch)" guarantee the nudge crash points exist to protect.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.runner.harness.adapter import WorkerHandle, WorkerPreamble
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.loop.produces import ProducesReconciler
from blizzard.runner.loop.steps import Advance, Pull
from blizzard.runner.store.repository import NewLease
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.graph import ProducesEntry
from tests.runner_fakes import (
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


class _AttachingOnNudgeHarness(FakeHarness):
    """A :class:`FakeHarness` whose SECOND ``judge`` call — the nudge resume — attaches
    on the worker's behalf, standing in for a worker that runs
    ``blizzard runner attach`` in response to the nudge it was resumed with."""

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

    def judge(
        self,
        workdir: str,
        session_id: str,
        judgement_prompt: str,
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        model: str | None = None,
        compaction_window: str | None = None,
    ) -> str:
        output = super().judge(workdir, session_id, judgement_prompt, preamble=preamble, chunk_id=chunk_id)
        if len(self.judged) == 2:  # the nudge resume, not the original verdict elicitation
            self._store.record_attachment(
                lease_id=self._lease_id,
                chunk_id=self._chunk_id,
                node_id=self._node_id,
                epoch=self._epoch,
                name=self._name,
                content=self._content,
                attached_at=self._clock.now(),
            )
        return output


class _DeclaringGitCommitOnNudgeHarness(FakeHarness):
    """A :class:`FakeHarness` whose SECOND ``judge`` call — the nudge resume — declares
    a git commit on the worker's behalf, standing in for a worker that pushes and
    declares in response to the nudge (issue #143 re-review)."""

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

    def judge(
        self,
        workdir: str,
        session_id: str,
        judgement_prompt: str,
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        model: str | None = None,
        compaction_window: str | None = None,
    ) -> str:
        output = super().judge(workdir, session_id, judgement_prompt, preamble=preamble, chunk_id=chunk_id)
        if len(self.judged) == 2:  # the nudge resume, not the original verdict elicitation
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
        return output


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
def test_nudge_fires_once_lists_missing_name_and_picks_up_the_attach(tmp_path: Path) -> None:
    """A `produces` name with no git commit and no attachment gets exactly one resumed
    nudge naming it; if the worker attaches in response, completion assembly prefers
    that content over the assessment fallback (the re-read-after-resume contract)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_review", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = _AttachingOnNudgeHarness(
        store=store,
        clock=clock,
        lease_id="lease_r",
        chunk_id="ch_1",
        node_id="nd_review",
        epoch=1,
        name="review-findings",
        content="attached during the nudge",
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

    Advance(ctx).run()
    Pull(ctx).run()

    # Two harness resumes: the original verdict elicitation, then exactly one nudge.
    assert len(harness.judged) == 2, "expected the verdict resume plus exactly one nudge resume"
    _, _, nudge_prompt = harness.judged[1]
    assert "review-findings" in nudge_prompt, "the nudge did not name the missing produces name"
    assert "blizzard runner artifact create --name review-findings" in nudge_prompt
    assert "blizzard runner attach" not in nudge_prompt

    # Both resumes instruct an attach, so both must carry the per-lease identity — a
    # preamble with a re-minted token — or the attach they elicit cannot authenticate.
    assert all(p is not None and p.lease_id == "lease_r" and p.lease_token for p in harness.judge_preambles)

    assert store.nudge_fired("lease_r", 1) is True

    _, submission = hub.completions[0]
    by_name = {a.name: a for a in submission.artifacts}
    assert by_name["review-findings"].kind is ArtifactKind.ASSET
    assert by_name["review-findings"].content == "attached during the nudge"
    assert by_name["review-findings"].attached is True


@pytest.mark.component
def test_nudge_fires_once_and_picks_up_a_git_commit_declared_mid_nudge(tmp_path: Path) -> None:
    """The mirror of the asset-only nudge-harvest test above, but for a `git_commit`
    spec: a worker's declaration in response to the nudge must fold into THIS attempt's
    completion, not just land durably for some later ADVANCE pass to notice."""
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
    harness = _DeclaringGitCommitOnNudgeHarness(
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

    Advance(ctx).run()
    Pull(ctx).run()

    # Two harness resumes: the original verdict elicitation, then exactly one nudge.
    assert len(harness.judged) == 2, "expected the verdict resume plus exactly one nudge resume"
    _, _, nudge_prompt = harness.judged[1]
    assert "commit" in nudge_prompt and "git_commit" in nudge_prompt
    assert "blizzard runner artifact commit" in nudge_prompt

    assert store.nudge_fired("lease_r", 1) is True

    _, submission = hub.completions[0]
    git_artifacts = [a for a in submission.artifacts if a.kind is ArtifactKind.GIT_COMMIT]
    assert len(git_artifacts) == 1, "the mid-nudge git-commit declaration never reached the completion"
    assert git_artifacts[0].repo == "toy-api"
    assert git_artifacts[0].branch_name == "feature/worker-declared"
    assert git_artifacts[0].commit_hash == "deadbeef"
    # The `git_commit`-kind spec is satisfied by kind, never asset-collected by its own
    # spec name — no phantom `commit` ASSET should ride alongside it.
    assert [a for a in submission.artifacts if a.name == "commit"] == []
    assert not any(a.kind is ArtifactKind.ASSET for a in submission.artifacts)


@pytest.mark.component
def test_nudge_resume_records_its_own_usage_fact(tmp_path: Path) -> None:
    """The nudge resume is its own harness invocation and burns its own tokens (epic
    #57/#58) — it must not go unrecorded just because its verdict reply is discarded.
    Isolated by scripting `usage_by_kind` to return a sample for `nudge` alone."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_review", epoch=1)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    nudge_sample = UsageSample(
        kind="nudge",
        model="fake-model",
        input_tokens=11,
        output_tokens=22,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.05,
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"),
        verdict="fail",
        assessment="the shared assessment",
        usage_by_kind={"nudge": nudge_sample},
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

    Advance(ctx).run()
    Pull(ctx).run()

    assert len(harness.judged) == 2, "expected the verdict resume plus exactly one nudge resume"

    totals = store.usage_since(datetime(2026, 7, 19, 0, 0, 0, tzinfo=UTC))
    assert totals.input_tokens == 11
    assert totals.output_tokens == 22
    assert totals.cost_usd == 0.05


@pytest.mark.component
def test_nudge_does_not_refire_on_a_second_advance_pass(tmp_path: Path) -> None:
    """The durable fact is honored: a re-drive of the same (lease, epoch) — the shape a
    crash between the fired-fact write and the completion buffer leaves behind — nudges
    no second time, converging straight to submission with the assessment fallback."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(_NOW)
    _seed_exited_lease(store, lease_id="lease_r", chunk_id="ch_1", node_id="nd_review", epoch=1)
    # Simulate recovery from a crash at `nudge.after-fired-fact.before-resume`: the
    # fact is durable, the resume it guards may or may not have actually run.
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
    Advance(ctx)._advance_exited_worker(lease)
    Pull(ctx).run()  # the flusher delivers the buffered completion to the hub

    # Only the original verdict elicitation — no nudge resume was attempted a second
    # time, because the durable fact already said this attempt's nudge was spent.
    assert len(harness.judged) == 1, "the nudge fired again despite the durable fact already being set"

    _, submission = hub.completions[0]
    by_name = {a.name: a for a in submission.artifacts}
    assert by_name["review-findings"].content == "the shared assessment"
    assert by_name["review-findings"].attached is False


@pytest.mark.component
def test_fully_attached_node_does_not_nudge(tmp_path: Path) -> None:
    """A node whose every `produces` name is already attached (or git-covered) elicits
    no nudge at all — the resume is a spawn primitive, not spent when nothing is
    missing."""
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

    Advance(ctx).run()
    Pull(ctx).run()

    assert len(harness.judged) == 1, "a fully-attached node must not be nudged"
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
