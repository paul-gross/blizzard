"""Authored post-merge edge (#63) — a `merged -> <node>` graph runs a node AFTER landing.

The e2e-tier proof of #63's routing substrate: a graph whose `deliver` hub node authors
`landed -> verify` (a post-merge runner node) instead of accepting the machinery default
(`landed -> done`). Landing every repo records a **non-terminal** transition into `verify`
and **retains** the route, so the runner advances the held chunk into `verify` in its warm
environment — a real worker node runs *after* the merge reached bare `main` — and only its
own `pass -> done` finalizes the chunk.

Asserted at both ends over the full live stack (mock forge + mock harness + fixture
workspace + real hub/runner, driven one synchronous tick at a time, every seam real, no
tokens/network):

* **git truth** — the build's file is reachable from the bare origin's `main` (the merge
  happened);
* **fleet truth** — the hub's transition history is `build -> deliver (landed) -> verify ->
  done`, so `verify` ran *after* the land (that transition exists only because a real
  worker completed the post-merge node), the chunk derives `done`, and its `landed` detail
  reads true even at `done` (informational, never a status — "merged", honestly).

This is the standing e2e smoke's companion to the crash-tier proof
(`tests/crash/test_kill9_sweep.py::test_kill9_at_hub_command_node_crash_point`), which
adds a `kill -9` in the `hubnode.after-step.before-marker` / `hubnode.after-marker.before-next`
windows around the post-merge node's own step; here the happy path is exercised without a
crash. Gated exactly like the sibling e2e scenarios — skipped unless `BLIZZARD_E2E=1` and
the sibling `blizzard-mock` worktree + a local winter source are discoverable.

Reproduce it — from the `blizzard` worktree in a provisioned feature env — with::

    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_post_merge_node_e2e.py
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _drive_until_done,
    _forge,
    _free_port,
    _git_bare,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _winter_source,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e post-merge node needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build: commit LANDED.md — the change that the deliver node merges to bare main.
_BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: land a change from the mock harness"],\n'
    "    check=True,\n"
    ")\n"
)
_BUILD_JUDGEMENT = "verdict('pass', 'committed the change; checks are green')\n"

# verify: a post-merge acceptance check. It makes no commit — its running is proof enough
# (the `verify -> done` transition exists only because this worker completed post-merge).
_VERIFY_SCRIPT = "pass\n"
_VERIFY_JUDGEMENT = "verdict('pass', 'the post-merge acceptance check is green')\n"


def _graph_yaml() -> str:
    """The `default-delivery` shape, but `deliver` authors `landed -> verify` (#63)."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_SCRIPT,
                "judgement": {
                    "prompt": _BUILD_JUDGEMENT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed; run the post-merge acceptance check.", "to": "verify"},
                        "conflict": {"description": "Conflict; back to build.", "to": "build"},
                    },
                },
            },
            "verify": {
                "executor": "runner",
                "prompt": _VERIFY_SCRIPT,
                "judgement": {
                    "prompt": _VERIFY_JUDGEMENT,
                    "choices": {"pass": {"description": "Green.", "to": "done"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_authored_landed_edge_runs_a_post_merge_node_after_landing(tmp_path: Path) -> None:
    """A `merged -> verify` graph merges, then runs `verify` post-merge, then lands `done`."""
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    scratch = tmp_path / "scratch"
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(winter_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "post-merge node", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, max_agents=1)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_until_done(config, hub, chunk_id, fenced)

        # Fleet truth: the chunk derived done — reachable ONLY through `verify -> done`.
        assert status == "done", f"chunk did not reach done (last status {status!r})"

        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        # The transition history proves `verify` ran AFTER the land: the `deliver -> verify`
        # edge is the authored non-terminal land, and `verify -> done` exists only because a
        # real post-merge worker completed the node.
        edges = [(t["from_node_name"], t["to_node_name"], t["choice_name"]) for t in detail["history"]]
        assert ("build", "deliver", "pass") in edges, edges
        assert ("deliver", "verify", "landed") in edges, edges
        # `verify -> done`: `done` is the reserved terminal, not a named graph node, so its
        # `to_node_name` is null — match on the raw `to_node_id`. This transition exists only
        # because a real post-merge worker completed `verify` after the land.
        assert any(
            t["from_node_name"] == "verify" and t["to_node_id"] == "done" and t["choice_name"] == "pass"
            for t in detail["history"]
        ), detail["history"]
        # `landed` is informational (never a status): a done chunk that merged reads true.
        assert detail["landed"] is True

        # The forge reports the PR merged — the delivery seam ran for real, before `verify`.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

    # Git truth: the build's file is reachable from the bare origin's main — the merge
    # happened, and the post-merge node ran against a genuinely landed change.
    tree = _git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "LANDED.md" in tree.split(), f"landed file not reachable from bare main:\n{tree}"
