"""A whole chunk delivered through the winter provider — the winter twin of the basic-provider landing.

The platform tiers land chunks over the built-in ``basic`` provider; this is the one end-to-end landing whose
subject is the winter binding: a real winter workspace minted around the fixture origins, a runner bound to it with
``provider="winter"``, and the scripted build committing into the worktree winter allocated."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

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
from tests.fleet_world import FIXTURE_ENV
from tests.host_files import with_workspace, write_host_file
from tests.winter.support import require_winter_source, winter_binding_gate

pytestmark = [pytest.mark.winter_binding, winter_binding_gate]

_ENV = "e1"


def test_winter_workspace_chunk_lands_through_the_winter_provider(tmp_path: Path) -> None:
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    source = require_winter_source()

    scratch = tmp_path / "scratch"
    minted = subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    assert minted.returncode == 0, f"fixture reset exited {minted.returncode}:\n{minted.stdout}\n{minted.stderr}"
    root = scratch / FIXTURE_ENV
    workspace, origins = root / "workspace", root / "origins"
    bare = origins / f"{REPO_NAME}.git"
    assert (workspace / ".winter" / "config.toml").is_file(), "the fixture was not minted as a winter workspace"
    (workspace / ".blizzard-mock-harness-fence").write_text("winter binding fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        graph = hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()})
        assert graph.status_code == 201, graph.text
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "winter landing", "body": "land a change"})
        assert issue.status_code == 201, issue.text
        created = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert created.status_code == 201, created.text
        chunk_id = created.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = with_workspace(
            _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port), provider="winter", envs=(_ENV,), repos=()
        )
        write_host_file(config)
        fenced = {**os.environ, MOCK_HARNESS_FENCE_VAR: "1"}
        assert _drive_until_done(config, hub, chunk_id, fenced) == "done"
        assert (workspace / _ENV / REPO_NAME / "LANDED.md").exists()

    assert "LANDED.md" in _git_bare(bare, "ls-tree", "-r", "--name-only", "main").split()
