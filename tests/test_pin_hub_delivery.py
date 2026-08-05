"""Pinning tests for hub-delivery decisions that were previously defended only by prose
(issue #270 phase 2, ``bzh:mutation-review-selection``).

Each test here converts one comment-defended decision into an assertion that fails if the
decision is reverted. Grouped by the module whose decision they pin.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from blizzard.foundation.ids import ARTIFACT_PREFIX, mint
from blizzard.hub.delivery.hub_node import UnconvergedDeliveryError
from blizzard.hub.delivery.marker_auth import MarkerAuthority
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.work import IWriteChunkRepository
from blizzard.hub.graphs import _GRAPHS_DIR, inline_graph_yaml
from blizzard.hub.graphs.scripts import land_pr_ci
from tests.support import FakeHubCommandRunner, FakeHubWorkdir, HubHarness, build_hub, pointer_token, report_lease

# -- marker_auth: the authority is instance-scoped, never persisted -------------------


@pytest.mark.unit
def test_a_restarted_processs_fresh_authority_refuses_a_prior_instances_token() -> None:
    """An orphaned land script — its owning hub process killed mid-land — presents a token
    minted by a process that no longer exists. The restarted process's authority is empty,
    so that write is refused rather than granted (issue #230)."""
    killed = MarkerAuthority()
    orphaned_token = killed.issue("ch_1", node_id="nd_1", epoch=1)

    restarted = MarkerAuthority()

    assert restarted.verify(orphaned_token, chunk_id="ch_1", node_id="nd_1", epoch=1) is False


# -- land_pr_ci: the terminal-failure findings write is unguarded ---------------------

_REPO = "acme/widget"
_BRANCH = "feat/x"
_COMMIT = "c" * 40
_CALLBACK_URL = "http://callback/hub-markers"


def _blocked_forge_with_a_terminal_check(calls: list[tuple[str, str, dict[str, Any] | None]], *, marker_status: int):
    """A double whose one open PR reads ``blocked`` with a completed-failing check run, and
    whose marker callback always answers ``marker_status``."""
    base = f"http://forge/repos/{_REPO}"
    check_run = {
        "id": 1,
        "name": "build",
        "status": "completed",
        "conclusion": "failure",
        "details_url": "https://forge/build/1",
        "head_sha": "headsha",
    }
    responses = {
        ("GET", f"{base}/pulls?state=open"): (200, [{"number": 1, "head": {"ref": _BRANCH, "sha": "headsha"}}]),
        ("GET", f"{base}/pulls/1"): (
            200,
            {
                "number": 1,
                "merged": False,
                "mergeable_state": "blocked",
                "head": {"ref": _BRANCH, "sha": "headsha"},
                "html_url": f"http://forge/{_REPO}/pull/1",
            },
        ),
        ("GET", f"{base}/commits/headsha/check-runs"): (200, {"total_count": 1, "check_runs": [check_run]}),
        ("GET", f"{base}/commits/main/check-runs"): (200, {"total_count": 0, "check_runs": []}),
    }

    def fake(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, url, body))
        if url == _CALLBACK_URL:
            return marker_status, {"message": "marker write failed"}
        return responses[(method, url)]

    return fake


@pytest.mark.unit
def test_a_terminal_failure_findings_write_failure_exits_non_zero_instead_of_routing_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The terminal-CI-failure findings write is deliberately unguarded: an unwritten
    set of findings is the only signal a resolve worker has nothing to read, so it exits
    non-zero rather than printing the `failure` edge over it (issue #243)."""
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps([{"repo": _REPO, "branch": _BRANCH, "commit": _COMMIT}]))
    monkeypatch.setenv("BZ_HUB_MARKER_CALLBACK_URL", _CALLBACK_URL)
    monkeypatch.setenv("BZ_HUB_MARKER_TOKEN", "test-marker-token")
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.delenv("BZ_FORGE_OWNER", raising=False)
    monkeypatch.delenv("BZ_FORGE_TOKEN", raising=False)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_pr_ci, "forge_request", _blocked_forge_with_a_terminal_check(calls, marker_status=500))

    exit_code = land_pr_ci.main()

    assert exit_code == 1
    assert land_pr_ci._CI_FAILURE not in capsys.readouterr().out


# -- hub_node: an unconverged delivery routes the failure edge ------------------------


def _writable(hub: HubHarness) -> IWriteChunkRepository:
    """A test-only cast: ``HubHarness.services.chunks`` is read-typed
    (``bzh:controller-read-only``), but the live object is always the write-capable
    ``ChunkStore``."""
    return cast(IWriteChunkRepository, hub.services.chunks)


def _mint_and_claim(hub: HubHarness) -> tuple[str, dict[str, str]]:
    """Mint the packaged adv-dwf graph, ingest a chunk, repin it onto adv-dwf, and claim a
    route — the same sequence ``tests/test_delivery_conflict_routing.py`` uses."""
    definition_yaml = inline_graph_yaml(_GRAPHS_DIR / "advanced-development-workflow" / "graph.yaml")
    minted = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in minted.json()["nodes"]}
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    ).json()["chunk_id"]
    repin = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": graph_id})
    assert repin.status_code == 202, repin.text
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    return chunk_id, nodes


def _seed_at_deliver_with_two_branches_for_one_repo(hub: HubHarness, chunk_id: str, nodes: dict[str, str]) -> None:
    """Place the chunk at ``deliver`` carrying two ``git_commit`` artifacts naming ONE repo
    on different branches at the same epoch — a chunk that worked the repo across two
    environments and never converged."""
    rows = [
        ArtifactRow(
            kind=ArtifactKind.GIT_COMMIT,
            name="w",
            data=f"{branch}:{commit * 40}",
            repo="acme/widget",
            forge=None,
            artifact_id=mint(ARTIFACT_PREFIX, hub.clock),
            chunk_id=chunk_id,
            node_id=nodes["build"],
            node_name="build",
            epoch=1,
        )
        for branch, commit in (("feat/from-e1", "a"), ("feat/from-e2", "b"))
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
    )


@pytest.mark.component
def test_an_unconverged_delivery_routes_the_failure_edge_instead_of_escaping_the_tick(tmp_path: Path) -> None:
    """A defect in what reached the node, not a fault in running it: the executor routes
    `deliver`'s authored `failure` edge and records the reason as an artifact, rather than
    letting the error escape and re-crash the tick on every poll."""
    runner = FakeHubCommandRunner()
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_two_branches_for_one_repo(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    chunk = hub.services.chunks.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    node = graph.node_by_id(nodes["deliver"])
    assert node is not None

    try:
        result = hub.services.hub_node.run(chunk, graph, node, epoch=1)
    except UnconvergedDeliveryError as exc:
        pytest.fail(f"an unconverged set must route `failure`, not escape the tick: {exc}")

    assert result is not None
    assert result.outcome_choice == "failure"
    assert result.to_node_name == "resolve"
    assert runner.calls == [], "the land script must never run against an unconverged set"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert any("unconverged-delivery" in a["name"] for a in detail["artifacts"]), detail["artifacts"]
