"""Cross-graph migration (issue #90) — scenario 10 of the standing e2e smoke.

A worker at the source graph's `build` node selects a cross-graph judgement choice
(`to: graph:triage-delivery`). Taking it ends the attempt, re-pins the chunk to the
target graph, releases the route, and re-queues it at the target's own `build` node
(name-match-else-entry). A claim under the new graph runs it to `done` — landing the
change on bare `main` exactly once.

Asserted at both ends over the full live stack (mock forge + mock harness + fixture
workspace + real hub/runner, driven one synchronous tick at a time, every seam real, no
tokens/network):

* **git truth** — the change is reachable from the bare origin's `main` (the merge
  happened under the TARGET graph, the only branch that lands it);
* **fleet truth** — the hub recorded a migration (never a transition spanning graphs),
  the chunk's `graph_id` is re-pinned to the target, its history spans two graphs (a
  migration step from the source's `build` plus the target graph's own
  `build -> deliver -> done`), and it derives `done`.

The served board renders this two-graph timeline through the same `MigrationView`/history
union the API returns here, and the standalone `/graphs` explorer — which reads graphs,
not chunks — is unaffected by a migrated chunk; both are covered generically by
`test_board_browser_e2e` / `test_graphs_diagram_browser_e2e`, so this scenario asserts the
git + fleet truth the browser views render from. Gated like its siblings — skipped unless
`BLIZZARD_E2E=1` and the sibling `blizzard-mock` worktree + a local winter source are
discoverable.

Reproduce it — from the `blizzard` worktree in a provisioned feature env — with::

    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_migration_e2e.py
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
        reason="e2e cross-graph migration needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# The source build makes no commit — it hands the chunk off. The target graph's own build
# node (below) does the real work, so the only branch that lands the change is the
# target's, keeping the exactly-once-on-main assertion honest.
_SOURCE_BUILD_SCRIPT = "pass\n"
_SOURCE_JUDGEMENT = "verdict('migrate', 'hand the chunk to the triage-delivery graph')\n"

# The target build commits the change the target's deliver node merges to bare main.
_TARGET_BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness after a migration\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: land a change after migrating graphs"],\n'
    "    check=True,\n"
    ")\n"
)
_TARGET_JUDGEMENT = "verdict('pass', 'committed the change; checks are green')\n"


def _source_yaml() -> str:
    """`default-delivery` (so ingest pins it) whose `build` migrates to `triage-delivery`."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _SOURCE_BUILD_SCRIPT,
                "judgement": {
                    "prompt": _SOURCE_JUDGEMENT,
                    "choices": {
                        "migrate": {"description": "Hand off to triage-delivery.", "to": "graph:triage-delivery"}
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _target_yaml() -> str:
    """`triage-delivery` — a build -> deliver graph whose `build` name-matches the source's."""
    import yaml

    graph = {
        "name": "triage-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _TARGET_BUILD_SCRIPT,
                "judgement": {
                    "prompt": _TARGET_JUDGEMENT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict; back to build.", "to": "build"},
                    },
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_cross_graph_migration_repins_requeues_and_lands_under_the_new_graph(tmp_path: Path) -> None:
    """A worker's cross-graph choice migrates the chunk, which then lands under the target graph."""
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
        # Mint the target first (so the source's cross-graph choice resolves at mint without a
        # warning), then the source that ingest pins.
        target = hub.post("/api/graphs", json={"definition_yaml": _target_yaml()})
        assert target.status_code == 201, target.text
        target_graph_id = target.json()["graph_id"]
        assert hub.post("/api/graphs", json={"definition_yaml": _source_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "migration", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, max_agents=1)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"

        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        # Fleet truth: the chunk was re-pinned to the target graph and its history spans two.
        assert detail["graph_id"] == target_graph_id, "the chunk was not re-pinned to the target graph"
        assert len(detail["migrations"]) == 1, "no migration step in the two-graph history"
        migration = detail["migrations"][0]
        assert migration["from_node_name"] == "build" and migration["to_graph_name"] == "triage-delivery"
        assert migration["landed_node_name"] == "build"  # name-match-else-entry landed on the target's build
        # The target graph's own steps ran after the migration: build -> deliver -> done.
        edges = [(t["from_node_name"], t["to_node_name"], t["choice_name"]) for t in detail["history"]]
        assert ("build", "deliver", "pass") in edges, edges
        assert any(
            t["from_node_name"] == "deliver" and t["to_node_id"] == "done" and t["choice_name"] == "landed"
            for t in detail["history"]
        ), detail["history"]

        # The forge reports the PR merged — the delivery seam ran under the target graph.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

    # Git truth: the change is reachable from bare main exactly once — only the target
    # graph's build branch lands it.
    tree = _git_bare(origin_bare, "log", "--oneline", "--", "LANDED.md")
    commits = [line for line in tree.splitlines() if line.strip()]
    assert len(commits) == 1, f"LANDED.md landed {len(commits)} times on bare main:\n{tree}"
