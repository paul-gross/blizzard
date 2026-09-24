"""A whole chunk delivered from an ordinary folder with the built-in git provider."""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from blizzard.runner.config import RunnerConfig, WorkspaceRepo
from tests.e2e.test_acceptance_loop import (
    MOCK_HARNESS_FENCE_VAR,
    REPO,
    REPO_NAME,
    _drive_until_done,
    _forge,
    _free_port,
    _git_bare,
    _graph_yaml,
    _hub,
    _mock_bin_dir,
    _runner_config,
)


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


def basic_build_prompt(build: str, *, branch: str, reset: bool = False) -> str:
    # The mock runs inside the environment, but a real worker starts at the
    # workspace root. Both resolve git paths from the injected workdir.
    # Git uses the injected workdir; artifact declaration uses the repo's name.
    repo_path = f"str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / {REPO_NAME!r})"
    build = build.replace(f"repo = {REPO_NAME!r}", f"repo = {REPO_NAME!r}\nrepo_path = {repo_path}")
    build = build.replace("pathlib.Path(repo)", "pathlib.Path(repo_path)")
    build = build.replace('["git", "-C", repo', '["git", "-C", repo_path')
    return (
        "import os, pathlib, subprocess\n"
        f"subprocess.run(['git', '-C', {repo_path}, 'switch', {'-C' if reset else '-c'!r}, {branch!r}], "
        "check=True)\n" + build
    )


def basic_graph_yaml() -> str:
    graph = yaml.safe_load(_graph_yaml())
    # The provider allocates a clean detached worktree. The worker owns its branch.
    graph["nodes"]["build"]["prompt"] = basic_build_prompt(
        graph["nodes"]["build"]["prompt"], branch="feature/basic-chunk"
    )
    return yaml.safe_dump(graph, sort_keys=False)


def basic_config(runner_dir: Path, workspace: Path, bin_dir: Path, hub_port: int, origin: Path) -> RunnerConfig:
    config = dataclasses.replace(
        _runner_config(runner_dir, workspace, bin_dir, hub_port),
        workspace_provider="basic",
        workspace_repos=(WorkspaceRepo(REPO_NAME, f"file://{origin}"),),
    )
    config.config_path.write_text(config.to_toml())
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
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("provision the sibling blizzard-mock worktree")

    winter_invoked = trap_winter(tmp_path, monkeypatch)
    bare = bare_origin(tmp_path)
    workspace = tmp_path / "ordinary-workspace"
    workspace.mkdir()
    (workspace / ".blizzard-mock-harness-fence").write_text("basic workspace test\n")
    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, bare.parent, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
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
        assert _drive_until_done(config, hub, chunk_id, fenced) == "done"
        assert (workspace / chunk_id / REPO_NAME / "LANDED.md").exists()
        assert (workspace / "projects" / REPO_NAME).is_dir()
        assert not (workspace / ".winter").exists()

    assert "LANDED.md" in _git_bare(bare, "ls-tree", "-r", "--name-only", "main").split()
    assert not winter_invoked.exists(), "the basic runner invoked winter"
