"""A declaration is accepted until the lease's outcome is buffered and refused after — kept under concurrency.

Real store, real services and judgement; doubles sit only at the provider, git and hub seams. A declaration is injected
at a named moment by wrapping the seam that precedes it, and one case admits on a second thread against a capture
unit held open, which is what pins the lock rather than call order."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.runner.environments.workspaces import WorkspaceRegistry
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.hub.outbound_buffer import COMPLETION_KIND
from blizzard.runner.leases.git_commit_declaration import GitCommitDeclarationTooLate
from blizzard.runner.leases.model import Lease, NewLease
from blizzard.runner.leases.worker_lease import WorkerLease
from blizzard.runner.lifecycle.judgement.git_commit_declaration import GitCommitDeclarationService
from blizzard.runner.lifecycle.judgement.git_commits import DeclaredCommits
from blizzard.runner.lifecycle.judgement.judgement import Judgement
from blizzard.runner.node_steps.submissions import CompletionArtifact
from blizzard.runner.operator.attachments import AttachmentService, AttachmentTooLate
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_store,
    single_workspace,
)
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_ORIGIN = "git@github.com:acme/widget.git"
_LEASE = "lease_1"


@dataclass
class _World:
    store: SqlAlchemyRunnerStore
    judgement: Judgement
    lease: Lease
    attach: AttachmentService
    declare: GitCommitDeclarationService
    commits: DeclaredCommits

    @property
    def worker(self) -> WorkerLease:
        return WorkerLease(self.lease, active=True)

    def declare_commit(self, commit: str) -> None:
        self.declare.declare(self.worker, repo="widget", branch="feat/x", commit=commit)

    def buffer(self) -> None:
        """Capture the outcome the way a judgement does — a completion carrying the pointers and the attachments."""

        def enqueue(unit: object, pointers: list[CompletionArtifact]) -> tuple[str, int]:
            attachments = self.store.attachments_for_lease(_LEASE, unit=unit)  # type: ignore[arg-type]
            body = {"pointers": [p.commit_hash for p in pointers], "attachments": attachments}
            seq = self.store.enqueue_outbound(
                kind=COMPLETION_KIND,
                chunk_id="ch_1",
                lease_id=_LEASE,
                payload=json.dumps(body),
                created_at=_NOW,
                unit=unit,  # type: ignore[arg-type]
            )
            return COMPLETION_KIND, seq

        self.judgement._capture(self.commits, enqueue)  # pyright: ignore[reportPrivateUsage]

    def buffered_body(self) -> dict[str, object]:
        (fact,) = [f for f in self.store.pending_outbound() if f.kind == COMPLETION_KIND]
        return json.loads(fact.payload)


def _world(tmp_path: Path, commits_cls: type[DeclaredCommits] = DeclaredCommits) -> _World:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=_LEASE,
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
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    provider = FakeProvider({"e1": "/ws/e1"}, repos={"e1": [("widget", _ORIGIN)]})
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=provider,
        harness=FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict="pass"),
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
    )
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    bindings = store.bindings_for_chunk("ch_1")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    workspaces: WorkspaceRegistry = single_workspace(provider, workspace_id="ws1")  # type: ignore[assignment]
    clock = FixedClock(_NOW)
    return _World(
        store=store,
        judgement=Judgement(ctx, lease, envelope, bindings),
        lease=lease,
        attach=AttachmentService(store, clock, store, store),
        declare=GitCommitDeclarationService(
            store, clock, workspaces, environments=store, outbound=store, lease_lock=store
        ),
        commits=commits_cls(ctx, lease, bindings),
    )


class _DeclaresAfterFirstVerify(DeclaredCommits):
    """Admits a commit declaration right after the first confirmation — between ``verify`` and the capture unit."""

    injected: str = "c-late"
    done: bool = False

    def verify(self) -> list[CompletionArtifact]:
        artifacts = super().verify()
        if not self.done:
            self.done = True
            store = self.ctx.stores.git_commit_declarations
            assert isinstance(store, SqlAlchemyRunnerStore)
            store.record_git_commit_declaration(
                lease_id=_LEASE,
                chunk_id="ch_1",
                node_id="nd_build",
                epoch=1,
                environment_id="e1",
                repo="widget",
                branch="feat/x",
                commit=self.injected,
                declared_at=_NOW,
            )
        return artifacts


def test_a_commit_declared_after_the_final_verify_rides_the_buffered_completion(tmp_path: Path) -> None:
    world = _world(tmp_path, _DeclaresAfterFirstVerify)
    world.declare_commit("c-early")
    world.buffer()
    assert world.buffered_body()["pointers"] == ["c-late"]


def test_an_attachment_declared_before_capture_rides_it(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.attach.attach(world.worker, name="notes", content="n1")
    world.buffer()
    assert world.buffered_body()["attachments"] == {"notes": "n1"}


def test_a_re_declaration_before_capture_rides_once_as_the_latest(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.declare_commit("c1")
    world.declare_commit("c2")
    world.buffer()
    assert world.buffered_body()["pointers"] == ["c2"]


def test_declarations_after_capture_are_refused_and_write_nothing(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.buffer()
    with pytest.raises(AttachmentTooLate):
        world.attach.attach(world.worker, name="late", content="x")
    with pytest.raises(GitCommitDeclarationTooLate):
        world.declare_commit("c-late")
    assert world.store.attachments_for_lease(_LEASE) == {}
    assert world.store.git_commit_declarations_for_lease(_LEASE) == {}


def test_the_acknowledgement_does_not_reopen_admission(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.buffer()
    world.store.ack_outbound_batch([f.seq for f in world.store.pending_outbound()], acked_at=_NOW)
    assert world.store.pending_submission_lease_ids() == set()
    with pytest.raises(AttachmentTooLate):
        world.attach.attach(world.worker, name="late", content="x")
    with pytest.raises(GitCommitDeclarationTooLate):
        world.declare_commit("c-late")


def test_a_rejudgement_of_an_acked_unclosed_lease_still_buffers(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.buffer()
    world.store.ack_outbound_batch([f.seq for f in world.store.pending_outbound()], acked_at=_NOW)
    world.buffer()
    assert len([f for f in world.store.pending_outbound() if f.kind == COMPLETION_KIND]) == 1


def test_the_takeover_reference_lease_still_takes_a_pointer_after_its_submission(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.buffer()
    reference = WorkerLease(world.lease, active=False)
    note = world.declare.declare(reference, repo="widget", branch="feat/x", commit="c-ref").note
    assert note is not None
    assert world.store.git_commit_declarations_for_lease(_LEASE)[("e1", "widget")].commit == "c-ref"
    with pytest.raises(Exception, match="closed"):
        world.attach.attach(reference, name="late", content="x")


def test_an_admission_on_another_thread_waits_for_the_open_capture_unit_and_is_refused(tmp_path: Path) -> None:
    world = _world(tmp_path)
    outcome: list[BaseException | None] = []
    started = threading.Event()

    def admit() -> None:
        started.set()
        try:
            world.attach.attach(world.worker, name="late", content="x")
            outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)

    with world.store.locked(_LEASE) as unit:
        thread = threading.Thread(target=admit)
        thread.start()
        started.wait()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "the admission ran through a held capture unit"
        world.store.enqueue_outbound(
            kind=COMPLETION_KIND, chunk_id="ch_1", lease_id=_LEASE, payload="{}", created_at=_NOW, unit=unit
        )
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], AttachmentTooLate)
    assert world.store.attachments_for_lease(_LEASE) == {}


def test_prune_keeps_an_acked_submission_of_an_unclosed_lease_and_drops_it_once_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.buffer()
    world.store.ack_outbound_batch([f.seq for f in world.store.pending_outbound()], acked_at=_NOW)
    later = _NOW + timedelta(days=30)
    # Only the acked `lease.minted` goes; the acked submission stays while its lease is unclosed.
    assert world.store.prune_outbound(now=later) == 1
    assert world.store.submission_buffered(_LEASE)
    world.store.record_closure(
        lease_id=_LEASE,
        chunk_id="ch_1",
        node_id="nd_build",
        reason=LeaseClosureReason.TRANSITIONED.value,
        closed_at=_NOW,
    )
    assert world.store.prune_outbound(now=later) == 1
    assert not world.store.submission_buffered(_LEASE)
