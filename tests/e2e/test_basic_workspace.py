"""A whole chunk delivered from an ordinary folder with the built-in git provider."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.provider import WorkspaceRepo
from tests.fleet_launchers import (
    MOCK_HARNESS_FENCE_VAR,
    REPO,
    drive_until_done,
    git_bare,
    mock_bin_dir,
    run_forge,
    run_hub,
    runner_config,
)
from tests.fleet_world import REPO_NAME, graph_yaml
from tests.host_files import with_workspace, write_host_file
from tests.support import free_port


def bare_origin(tmp_path: Path) -> Path:
    """Only git: no winter fixture or winter workspace is used to mint this world."""
    origins = tmp_path / "origins"
    origins.mkdir()
    bare = origins / f"{REPO_NAME}.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.email", "test@example.com"), ("config", "user.name", "Test")):
        subprocess.run(["git", "-C", str(seed), *args], check=True, capture_output=True)
    (seed / "README.md").write_text("# toy-api\n")
    for args in (
        ("add", "-A"),
        ("commit", "-m", "seed"),
        ("remote", "add", "origin", str(bare)),
        ("push", "origin", "main"),
    ):
        subprocess.run(["git", "-C", str(seed), *args], check=True, capture_output=True)
    return bare


def basic_graph_yaml() -> str:
    """The scripted graph, whose build already switches to its own branch on the detached worktree."""
    return graph_yaml()


def basic_config(runner_dir: Path, workspace: Path, bin_dir: Path, hub_port: int, origin: Path) -> RunnerConfig:
    config = with_workspace(
        runner_config(runner_dir, workspace, bin_dir, hub_port), repos=(WorkspaceRepo(REPO_NAME, f"file://{origin}"),)
    )
    write_host_file(config)
    return config


def trap_winter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fail the exercise if any child tries to invoke winter, even if it is installed."""
    bin_dir = tmp_path / "no-winter"
    bin_dir.mkdir()
    invoked = tmp_path / "winter-invoked"
    winter = bin_dir / "winter"
    winter.write_text(f"#!/bin/sh\ntouch '{invoked}'\nexit 99\n")
    winter.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return invoked


@pytest.mark.e2e
def test_basic_workspace_chunk_lands_without_winter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("BLIZZARD_E2E") != "1":
        pytest.skip("set BLIZZARD_E2E=1 to drive the real mock fleet")
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("provision the sibling blizzard-mock worktree")

    winter_invoked = trap_winter(tmp_path, monkeypatch)
    bare = bare_origin(tmp_path)
    workspace = tmp_path / "ordinary-workspace"
    workspace.mkdir()
    (workspace / ".blizzard-mock-harness-fence").write_text("basic workspace test\n")
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, bare.parent, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert forge.get(f"/repos/{REPO}").status_code == 200
        graph = hub.post("/api/graphs", json={"definition_yaml": basic_graph_yaml()})
        assert graph.status_code == 201, graph.text
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "basic workspace", "body": "land a change"})
        assert issue.status_code == 201, issue.text
        created = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert created.status_code == 201, created.text
        chunk_id = created.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = basic_config(tmp_path / "runner", workspace, bin_dir, hub_port, bare)
        fenced = {**os.environ, MOCK_HARNESS_FENCE_VAR: "1"}
        assert drive_until_done(config, hub, chunk_id, fenced) == "done"
        assert (workspace / chunk_id / REPO_NAME / "LANDED.md").exists()
        assert (workspace / "projects" / REPO_NAME).is_dir()
        assert not (workspace / ".winter").exists()

    assert "LANDED.md" in git_bare(bare, "ls-tree", "-r", "--name-only", "main").split()
    assert not winter_invoked.exists(), "the basic runner invoked winter"
