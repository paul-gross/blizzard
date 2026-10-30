"""A freshly set-up runner is offered work on its first registration, with no configuration edit between.

``blizzard-runner init`` against a real hub daemon scaffolds a config that serves ``default``; one
``blizzard-runner tick`` registers the runner, which stores the hub's answer; and the hub's matched peek,
asked under the runner's own token, offers it a ready chunk of ``default``. The chunk stays in the backlog
through the tick, so the tick only registers and the peek that follows is the one under test. Needs the
sibling blizzard-mock worktree for its forge, like ``tests/e2e/test_basic_workspace.py``."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from blizzard.runner.cli.runtime import read_stores
from blizzard.runner.host_layout import HostLayout
from tests.fleet_launchers import REPO, mock_bin_dir, run_forge, run_hub
from tests.fleet_world import REPO_NAME, bare_origin
from tests.host_files import load_runner_config
from tests.runner_join import RUNNER_BIN, join_runner, runner_spawn_env
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e runner first contact needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

#: A capability set that admits any chunk declaring no harness preference — the packaged default graph's.
_DEFAULT_CAPABILITY = [{"harness_id": "claude", "default": True}]


def test_a_freshly_initialized_runner_is_offered_a_default_chunk_on_first_registration(tmp_path: Path) -> None:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("provision the sibling blizzard-mock worktree")

    bare = bare_origin(tmp_path)
    forge_port, hub_port = free_port(), free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    with run_forge(bin_dir, bare.parent, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        (default,) = hub.get("/api/projects").json()["projects"]
        assert default["slug"] == "default"
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "first contact", "body": "offered on arrival"})
        assert issue.status_code == 201, issue.text
        created = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert created.status_code == 201, created.text
        chunk_id = created.json()["chunk_id"]

        runner_dir = tmp_path / "runner"
        joined = join_runner(runner_dir, hub_url)
        config = load_runner_config(runner_dir)
        assert config.projects == ("default",)
        scaffolded = HostLayout(runner_dir).config_path.read_bytes()
        added = hub.get(f"/api/runners/{joined.runner_id}")
        assert added.status_code == 200, added.text
        assert added.json()["registered_at"] is None

        ticked = subprocess.run(
            [str(RUNNER_BIN), "tick", "--dir", str(runner_dir)],
            capture_output=True,
            text=True,
            env=runner_spawn_env(hub_url),
            check=False,
            timeout=120,
        )
        assert ticked.returncode == 0, f"runner tick failed ({ticked.returncode}):\n{ticked.stdout}\n{ticked.stderr}"
        assert HostLayout(runner_dir).config_path.read_bytes() == scaffolded

        registered = hub.get(f"/api/runners/{joined.runner_id}").json()
        assert registered["registered_at"] is not None
        assert [(p["entry"], p["project_id"]) for p in registered["projects"]] == [("default", default["project_id"])]
        with read_stores(config) as stores:
            answer = stores.served_projects.served_projects_answer()
        assert answer is not None
        assert [(p.entry, p.project_id) for p in answer.projects] == [("default", default["project_id"])]

        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        peeked = hub.post("/api/fleet/queue/peek", json={"capabilities": _DEFAULT_CAPABILITY}, headers=joined.bearer)
        assert peeked.status_code == 200, peeked.text
        assert [(e["chunk_id"], e["project_id"], e["project_slug"]) for e in peeked.json()["entries"]] == [
            (chunk_id, default["project_id"], "default")
        ]
