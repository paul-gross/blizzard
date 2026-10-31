"""Fast-forward policy against the live mock forge and hub."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from tests.e2e.flow_support import _graph, flow_runner_config, flow_world, pinned_chunk, ticking_runner
from tests.e2e.test_flow_turns_e2e import _detail
from tests.fleet_launchers import REPO, run_forge, run_hub
from tests.fleet_world import REPO_NAME
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("BLIZZARD_E2E") != "1", reason="requires the live mock-fleet stack"),
]


@pytest.mark.parametrize("protected", [False, True])
def test_ahead_fast_forward_and_protected_base(tmp_path: Path, protected: bool) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        patched = hub.patch(f"/api/repositories/{REPO_NAME}", json={"landing_policy": "fast-forward"})
        assert patched.status_code == 200, patched.text
        if protected:
            armed = forge.post("/_levers/branch_protected", json={"repo": REPO, "branch": "main"})
            assert armed.status_code == 200, armed.text
        chunk = pinned_chunk(hub, forge, "FAST-FORWARD.md")
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            tick_until(lambda: _detail(hub, chunk)["status"] in {"done", "needs_human", "stopped"})
        detail = _detail(hub, chunk)
        assert detail["status"] == ("needs_human" if protected else "done"), detail
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"})
        assert pulls.status_code == 200 and pulls.json() == []


@pytest.mark.parametrize("stale_candidate", [False, True])
def test_diverged_base_creates_and_cleans_proven_candidate(tmp_path: Path, stale_candidate: bool) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.patch(f"/api/repositories/{REPO_NAME}", json={"landing_policy": "fast-forward"}).status_code == 200
        graph = yaml.safe_load(_graph("DIVERGED.md", (REPO_NAME,), poll_interval=1, record_findings=False))
        graph["nodes"]["build"]["prompt"] += (
            f"path = str(root / {REPO_NAME!r})\n"
            "subprocess.run(['git', '-C', path, 'fetch', 'origin', 'main'], check=True)\n"
            "subprocess.run(['git', '-C', path, 'switch', '-C', 'base-update', 'origin/main'], check=True)\n"
            "(pathlib.Path(path) / 'BASE-UPDATE.md').write_text('base moved\\n')\n"
            "subprocess.run(['git', '-C', path, 'add', '-A'], check=True)\n"
            "subprocess.run(['git', '-C', path, '-c', 'user.email=mock@blizzard.local', "
            "'-c', 'user.name=Mock Harness', 'commit', '-m', 'base update'], check=True)\n"
            "subprocess.run(['git', '-C', path, 'push', 'origin', 'HEAD:main'], check=True)\n"
        )
        if stale_candidate:
            graph["nodes"]["build"]["prompt"] += (
                "subprocess.run(['git', '-C', path, 'push', 'origin', "
                "commit + ':refs/heads/feature/diverged-landing'], check=True)\n"
            )
        response = hub.post("/api/graphs", json={"definition_yaml": yaml.safe_dump(graph, sort_keys=False)})
        assert response.status_code == 201, response.text
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "DIVERGED.md", "body": "diverged landing"})
        assert issue.status_code == 201, issue.text
        made = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert made.status_code == 201, made.text
        chunk = made.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk}/promote").status_code == 202
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            tick_until(lambda: _detail(hub, chunk)["status"] in {"done", "needs_human", "stopped"})
        detail = _detail(hub, chunk)
        assert (detail["status"], detail["landed"]) == ("done", True), detail
        base_tip = forge.get(f"/repos/{REPO}/git/ref/heads/main").json()["object"]["sha"]
        feature_tip = forge.get(f"/repos/{REPO}/git/ref/heads/feature/diverged").json()["object"]["sha"]
        commit = forge.get(f"/repos/{REPO}/commits/{base_tip}").json()
        assert len(commit["parents"]) == 2
        assert commit["parents"][1]["sha"] == feature_tip
        candidate = forge.get(f"/repos/{REPO}/git/ref/heads/feature/diverged-landing")
        assert candidate.status_code == 422
        assert candidate.json()["message"] == f"ref does not resolve in {REPO}: feature/diverged-landing"
