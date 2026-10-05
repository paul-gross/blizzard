"""One reused :class:`DeclaredCommits` across successive ``verify`` calls — memoized confirmations,
converged pointers, and the ``command-failed`` fact a disagreement emits exactly once.

Declarations vary through the real runner store; doubles sit only at the provider and git seams."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.repo_ref import repo_identity
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.leases import NewLease
from blizzard.runner.lifecycle.judgement.git_commits import DeclaredCommits
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_ORIGIN = "git@github.com:acme/widget.git"
_ENVS = ("e1", "e2")
_Pointer = tuple[str | None, str | None]


@dataclass
class _Lease:
    store: SqlAlchemyRunnerStore
    git: FakeWorktreeGit
    commits: DeclaredCommits

    def declare(self, env: str, commit: str) -> None:
        self.store.record_git_commit_declaration(
            lease_id="lease_1",
            chunk_id="ch_1",
            node_id="nd_build",
            epoch=1,
            environment_id=env,
            repo="widget",
            branch="feat/x",
            commit=commit,
            declared_at=_NOW,
        )

    def verified(self) -> list[_Pointer]:
        """``(branch, commit)`` of every pointer one ``verify`` returns."""
        return [(a.branch_name, a.commit_hash) for a in self.commits.verify()]

    def reports(self) -> list[dict[str, str]]:
        events = [json.loads(b.payload) for b in self.store.pending_outbound() if b.kind == EVENT_RECORDED]
        assert all(e["kind"] == "command-failed" for e in events)
        return [e["detail"] for e in events]


def _lease(tmp_path: Path) -> _Lease:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    for env in _ENVS:
        store.record_binding(chunk_id="ch_1", environment_id=env, workdir=f"/ws/{env}", bound_at=_NOW)
    git = FakeWorktreeGit()
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider(
            {env: f"/ws/{env}" for env in _ENVS}, repos={env: [("widget", _ORIGIN)] for env in _ENVS}
        ),
        harness=FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict="pass"),
        probe=FakeProbe(),
        worktree_git=git,
    )
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    return _Lease(store, git, DeclaredCommits(ctx, lease, store.bindings_for_chunk("ch_1")))


def _disagreement(*members: tuple[str, str]) -> dict[str, str]:
    """The report naming each ``(env, commit)`` in confirmation order."""
    return {
        "command": f"converge commit pointers for repository {repo_identity(_ORIGIN, 'widget')!r}",
        "stderr_tail": "environments declare different pointers: "
        + "; ".join(f"{env!r} -> feat/x@{commit}" for env, commit in members),
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("declarations", "pointers", "reports"),
    [
        pytest.param({"e1": "c1"}, [("feat/x", "c1")], [], id="lone-pointer"),
        pytest.param({"e1": "c1", "e2": "c1"}, [("feat/x", "c1")], [], id="agreeing-pointers"),
        pytest.param(
            {"e1": "c1", "e2": "c2"},
            [("feat/x", "c1"), ("feat/x", "c2")],
            [_disagreement(("e1", "c1"), ("e2", "c2"))],
            id="disagreeing-pointers",
        ),
    ],
)
def test_first_verify_converges_and_reports_only_disagreement(
    tmp_path: Path, declarations: dict[str, str], pointers: list[_Pointer], reports: list[dict[str, str]]
) -> None:
    lease = _lease(tmp_path)
    for env, commit in declarations.items():
        lease.declare(env, commit)

    assert lease.verified() == pointers
    assert lease.reports() == reports
    assert lease.git.verified_calls == [(_ORIGIN, "feat/x", c) for c in declarations.values()]


@pytest.mark.unit
@pytest.mark.parametrize("declarations", [{"e1": "c1"}, {"e1": "c1", "e2": "c2"}], ids=["agreeing", "disagreeing"])
def test_unchanged_declarations_reverify_to_nothing(tmp_path: Path, declarations: dict[str, str]) -> None:
    lease = _lease(tmp_path)
    for env, commit in declarations.items():
        lease.declare(env, commit)
    lease.verified()
    calls, reports = list(lease.git.verified_calls), lease.reports()

    assert lease.verified() == []
    assert lease.git.verified_calls == calls
    assert lease.reports() == reports


@pytest.mark.unit
def test_later_disagreement_is_reported_once(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    lease.declare("e1", "c1")
    assert lease.verified() == [("feat/x", "c1")]

    lease.declare("e2", "c2")
    assert lease.verified() == [("feat/x", "c1"), ("feat/x", "c2")]
    assert lease.verified() == []

    assert lease.reports() == [_disagreement(("e1", "c1"), ("e2", "c2"))]
    assert lease.git.verified_calls == [(_ORIGIN, "feat/x", "c1"), (_ORIGIN, "feat/x", "c2")]


@pytest.mark.unit
def test_changed_redeclaration_replaces_the_stale_confirmation(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    lease.declare("e1", "c1")
    lease.declare("e2", "c1")
    assert lease.verified() == [("feat/x", "c1")]

    lease.declare("e1", "c3")
    assert sorted(lease.verified()) == [("feat/x", "c1"), ("feat/x", "c3")]

    assert lease.reports() == [_disagreement(("e2", "c1"), ("e1", "c3"))]
    assert lease.git.verified_calls == [(_ORIGIN, "feat/x", c) for c in ("c1", "c1", "c3")]


@pytest.mark.unit
def test_later_agreeing_pointer_resubmits_nothing(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    lease.declare("e1", "c1")
    assert lease.verified() == [("feat/x", "c1")]

    lease.declare("e2", "c1")
    assert lease.verified() == []

    assert lease.reports() == []
    assert lease.git.verified_calls == [(_ORIGIN, "feat/x", "c1"), (_ORIGIN, "feat/x", "c1")]
