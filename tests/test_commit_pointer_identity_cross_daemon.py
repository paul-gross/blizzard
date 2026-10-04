"""A commit pointer's repository identity, from the runner's submission through hub delivery — component tier.

Runs the runner's real :class:`DeclaredCommits` over two held environments, records what it submits
as the chunk's ``git_commit`` artifacts in a real hub, and runs the ``deliver`` node, capturing the
``BZ_HUB_GIT_COMMITS`` it hands the land script. Doubles sit only at the provider, git, and
command-runner seams."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import ARTIFACT_PREFIX, Id
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.leases import NewLease
from blizzard.runner.lifecycle.judgement.git_commits import DeclaredCommits
from blizzard.wire.completion import SubmittedArtifact
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, FakeWorktreeGit, make_context, make_store
from tests.support import FakeHubCommandRunner, FakeHubWorkdir, build_hub, report_lease
from tests.test_pin_hub_delivery import _mint_and_claim, _writable

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.component


def _submit(tmp_path: Path, *, origins: dict[str, str], commits: dict[str, str]) -> list[SubmittedArtifact]:
    """What the runner submits for two envs each declaring ``widget`` at ``commits[env]``."""
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
    for env in ("e1", "e2"):
        store.record_binding(chunk_id="ch_1", environment_id=env, workdir=f"/ws/{env}", bound_at=_NOW)
        store.record_git_commit_declaration(
            lease_id="lease_1",
            chunk_id="ch_1",
            node_id="nd_build",
            epoch=1,
            environment_id=env,
            repo="widget",
            branch="feat/x",
            commit=commits[env],
            declared_at=_NOW,
        )
    provider = FakeProvider(
        {"e1": "/ws/e1", "e2": "/ws/e2"}, repos={env: [("widget", origins[env])] for env in ("e1", "e2")}
    )
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
    return DeclaredCommits(ctx, lease, store.bindings_for_chunk("ch_1")).verify()


def _deliver_payload(tmp_path: Path, submitted: list[SubmittedArtifact]) -> list[dict[str, str]]:
    """Record ``submitted`` as the chunk's build artifacts, run ``deliver``, and return the
    ``BZ_HUB_GIT_COMMITS`` list the land script received."""
    runner = FakeHubCommandRunner()
    (tmp_path / "hub").mkdir()
    hub = build_hub(tmp_path / "hub", hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, nodes = _mint_and_claim(hub)
    rows = [
        StoredArtifact(
            kind=ArtifactKind.GIT_COMMIT,
            name=a.name,
            data=f"{a.branch_name}:{a.commit_hash}",
            repo=a.repo,
            forge=a.forge,
            artifact_id=Id.mint(ARTIFACT_PREFIX, hub.clock).value,
            chunk_id=chunk_id,
            node_id=nodes["build"],
            node_name="build",
            epoch=1,
        )
        for a in submitted
    ]
    _writable(hub).record_transition(
        transition_id="tr_seed_deliver",
        chunk_id=chunk_id,
        from_node_id=None,
        to_node_id=nodes["deliver"],
        choice_name=None,
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=rows,
        proposals=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    node = graph.node_by_id(nodes["deliver"])
    assert node is not None
    hub.services.hub_node.run(chunk, graph, node, epoch=1)
    assert runner.calls, "deliver never ran its land script"
    return json.loads(runner.calls[0][2]["BZ_HUB_GIT_COMMITS"])


def test_two_envs_on_one_origin_deliver_one_entry(tmp_path: Path) -> None:
    submitted = _submit(
        tmp_path,
        origins={"e1": "git@github.com:acme/widget.git", "e2": "https://github.com/acme/widget"},
        commits={"e1": "a" * 40, "e2": "a" * 40},
    )

    payload = _deliver_payload(tmp_path, submitted)

    assert payload == [{"repo": "acme/widget", "branch": "feat/x", "commit": "a" * 40}]


def test_one_name_at_two_owners_delivers_two_qualified_entries(tmp_path: Path) -> None:
    submitted = _submit(
        tmp_path,
        origins={"e1": "https://github.com/owner-a/widget", "e2": "https://github.com/owner-b/widget"},
        commits={"e1": "a" * 40, "e2": "b" * 40},
    )

    payload = _deliver_payload(tmp_path, submitted)

    assert {e["repo"] for e in payload} == {"owner-a/widget", "owner-b/widget"}
