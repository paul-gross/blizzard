"""Human-gate decision park→decide→deliver — scenario 5 of the e2e smoke — MVP criterion 12.

The other half of the human loop, end to end over the real stack (design/workflow-engine.md,
D-045/D-032): a graph whose ``build`` node passes into a **human gate ahead of deliver**.
The build worker makes a real commit and its verdict transitions the chunk *into* the
gate; the hub opens an **open Decision** carrying the build's artifacts and the chunk
derives **waiting_on_human** — the runner holds its warm environments, spawning nothing.
A human lists the gate with the real ``blizzard hub decisions`` and approves it with the
real ``blizzard hub decide``; the holding runner picks the resolution up on its next tick,
records the resolving transition along the ``approve`` edge, and the hub's deliver node
lands the build commit on the bare origin's ``main`` — the chunk reaches **done**.

This is the "sample-gate variant": the packaged ``build -> review -> deliver`` default with
a human ``approve-gate`` minted between the work and delivery (design sample graph, D-032).
Reuses the acceptance loop's live-stack scaffolding (forge/hub/runner harnesses, fixture
mint, port helpers). Skipped unless ``BLIZZARD_E2E=1`` with the sibling ``blizzard-mock``
worktree provisioned — exactly like the sibling scenarios.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import run_single_tick
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
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
        reason="e2e gate decision needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build: write a file and make a real commit — the work the gate stands in front of. The
# runner discovers the commit (HEAD ahead of base), pushes it, and its verdict transitions
# the chunk into the gate, carrying the commit as the decision's artifact.
_BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed behind a human gate\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: land a change awaiting sign-off"],\n'
    "    check=True,\n"
    ")\n"
)
# build judgement: pass into the gate. The gate itself is human-judged — the runner never
# executes a worker for it; the hub opens the decision on arrival.
_JUDGEMENT_SCRIPT = "verdict('pass', 'built and committed; awaiting human sign-off to ship')\n"


def _graph_yaml() -> str:
    """The sample-gate variant — ``build -> approve-gate (human) -> deliver``.

    Named ``default-delivery`` so the hub's lazy ``ensure_default`` reuses it by name
    (D-081). The ``approve-gate`` node is a runner node judged ``by: human`` (D-032): a
    transition into it parks the chunk on a decision; only the resolving transition
    (carrying the decision id) may leave it.
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_SCRIPT,
                "judgement": {
                    "prompt": _JUDGEMENT_SCRIPT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "approve-gate"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "approve-gate": {
                "executor": "runner",
                "judgement": {
                    "by": "human",
                    "choices": {
                        "approve": {"description": "Ship it — proceed to delivery.", "to": "deliver"},
                        "reject": {"description": "Send it back to build.", "to": "build"},
                    },
                },
            },
            "deliver": {"executor": "hub", "mode": "merge-to-main"},
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _tick_until(
    config: RunnerConfig, hub: httpx.Client, chunk_id: str, fenced: dict[str, str], targets: set[str], timeout: float
) -> str:
    """Drive synchronous ticks until the chunk reaches one of ``targets``; return its status."""
    prior = dict(os.environ)
    os.environ.update(fenced)  # the runner spawns the fenced mock harness in-process
    try:
        deadline = time.monotonic() + timeout
        status = "ready"
        while time.monotonic() < deadline:
            run_single_tick(config)
            status = hub.get(f"/api/chunks/{chunk_id}").json()["status"]
            if status in targets:
                return status
            time.sleep(0.5)
        return status
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _blizzard(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the real ``blizzard`` CLI (the installed console script) and capture output."""
    return subprocess.run([str(Path(sys.executable).parent / "blizzard"), *args], capture_output=True, text=True)


def test_graph_gate_parks_a_decision_then_decide_delivers(tmp_path: Path) -> None:
    """A human gate ahead of deliver parks a decision; `hub decide` approves and it lands."""
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
    hub_url = f"http://127.0.0.1:{hub_port}"
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "gate", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post(
            "/api/chunks",
            json={"pointers": [{"source": REPO_NAME, "ref": str(issue_number)}]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner (D-103)

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        # Phase 1: build commits and its verdict transitions into the gate — the hub opens
        # a decision carrying the build artifact and the chunk derives waiting_on_human.
        status = _tick_until(config, hub, chunk_id, fenced, {"waiting_on_human", "done", "needs_human"}, 90.0)
        assert status == "waiting_on_human", f"chunk did not park at the gate (last status {status!r})"

        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        decision = detail["decision"]
        assert decision is not None and decision["node_name"] == "approve-gate", detail
        assert {c["name"] for c in decision["choices"]} == {"approve", "reject"}
        decision_id = decision["decision_id"]
        # The open decision carries the build's work: the git-commit artifact landed on the
        # chunk with the transition into the gate (D-036), so deliver has something to merge.
        assert any(a["kind"] == "git_commit" for a in detail["artifacts"]), (
            f"the gated decision does not carry the build artifact: {detail['artifacts']}"
        )

        # The fleet surfaces the open decision — the real `blizzard hub decisions` verb.
        listed = _blizzard("hub", "decisions", "--hub-url", hub_url)
        assert listed.returncode == 0, listed.stderr
        assert decision_id in listed.stdout, f"`hub decisions` did not list the open decision:\n{listed.stdout}"

        # A human approves at the hub — the real `blizzard hub decide` verb (first-write-wins).
        decided = _blizzard("hub", "decide", decision_id, "approve", "--by", "alice", "--hub-url", hub_url)
        assert decided.returncode == 0, f"hub decide failed:\n{decided.stderr}"

        # Phase 2: the holding runner records the resolving transition; deliver lands the chunk.
        status = _tick_until(config, hub, chunk_id, fenced, {"done", "needs_human", "stopped"}, 90.0)
        assert status == "done", f"chunk did not land after the gate was decided (last status {status!r})"

        # Fleet truth: the decision is resolved-and-transitioned, off the open list.
        assert hub.get("/api/decisions").json()["decisions"] == []
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

    # Git truth: the build commit the gate approved is on the bare origin's main.
    tree = _git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "LANDED.md" in tree.split(), f"landed file not reachable from bare main:\n{tree}"
