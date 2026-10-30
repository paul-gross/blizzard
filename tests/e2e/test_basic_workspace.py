"""A whole chunk delivered from an ordinary folder with the built-in git provider."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.fleet_launchers import (
    MOCK_HARNESS_FENCE_VAR,
    REPO,
    drive_until_done,
    git_bare,
    mock_bin_dir,
    run_forge,
    run_hub,
)
from tests.fleet_world import REPO_NAME, bare_origin, basic_config, basic_graph_yaml, trap_winter
from tests.support import free_port


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
