"""A conflict at the default graph's `deliver` node lands ZERO repos (#67).

The e2e-tier proof that the default graph's authored `land_default.py` script's
check-then-push atomicity is real: with the mock forge's `merge_conflict` lever armed
for the fixture repo, the script's check stage finds the freshly-opened PR NOT cleanly
mergeable and prints `conflict` before ever attempting a push — nothing lands, the
bounce fact + envelope routes the chunk back to `build` (#64), and the route is
retained (environments held, the runner keeps the chunk running rather than
finalizing).

Asserted at both ends over the full live stack (mock forge + mock harness + fixture
workspace + real hub/runner), exactly like the sibling e2e scenarios:

* **fleet truth** — the chunk derives `running` back at `build`, its detail carries
  exactly one recorded bounce (cause `conflict`) and a `bounce-envelope` asset, and
  `landed` reads false (nothing landed via a `merged/<repo>` marker);
* **git truth** — bare `main` is exactly where it started; the forge holds no merged PR
  for the fixture repo.

Gated exactly like the sibling e2e scenarios — skipped unless `BLIZZARD_E2E=1` and the
sibling `blizzard-mock` worktree + a local winter source are discoverable.

Reproduce it — from the `blizzard` worktree in a provisioned feature env — with::

    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_delivery_conflict_e2e.py
"""

from __future__ import annotations

import os
import subprocess
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
        reason="e2e delivery-conflict needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "CONFLICTED.md").write_text("armed conflict\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: a change the armed conflict lever will reject"],\n'
    "    check=True,\n"
    ")\n"
)
_BUILD_JUDGEMENT = "verdict('pass', 'committed the change; checks are green')\n"


def _graph_yaml() -> str:
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
                "retries": {"max": 2, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict; back to build.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _drive_one_bounce(config: RunnerConfig, hub: httpx.Client, chunk_id: str, fenced_env: dict[str, str]) -> str:
    """Tick until the chunk is back at `build` (post-bounce) or reaches a terminal
    status — whichever comes first. A conflict never terminates the chunk (#64: a
    bounce is contention, not failure), so this stops on the FIRST bounce rather than
    driving to `done`, which a repo armed to always conflict would never reach."""
    prior = dict(os.environ)
    os.environ.update(fenced_env)
    try:
        deadline = time.monotonic() + 60.0
        status = "ready"
        while time.monotonic() < deadline:
            run_single_tick(config)
            detail = hub.get(f"/api/chunks/{chunk_id}")
            assert detail.status_code == 200, detail.text
            body = detail.json()
            status = body["status"]
            if status in {"done", "stopped", "needs_human"}:
                return status
            if body["bounces"]:
                return status
            time.sleep(0.5)
        return status
    finally:
        os.environ.clear()
        os.environ.update(prior)


def test_conflict_lands_zero_repos_and_routes_the_bounce_envelope_back_to_build(tmp_path: Path) -> None:
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

    main_before = _git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # Arm the mock forge's merge_conflict lever for the fixture repo — repo-scoped
        # (no PR number), so it applies to whichever PR the script opens.
        armed = forge.post("/_levers/merge_conflict", json={"repo": REPO})
        assert armed.status_code == 200, armed.text

        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "armed conflict", "body": "the conflict chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_one_bounce(config, hub, chunk_id, fenced)

        # Fleet truth: never terminal — a bounce is contention, not failure (#64).
        assert status == "running", f"conflict chunk did not bounce back to running (last status {status!r})"
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert detail["current_node_name"] == "build"
        assert len(detail["bounces"]) == 1
        assert detail["bounces"][0]["cause"] == "conflict"
        bounce_assets = [a for a in detail["artifacts"] if a["name"] == "bounce-envelope"]
        assert len(bounce_assets) == 1, detail["artifacts"]
        # Zero repos landed — nothing pushed once the check stage found a conflict.
        assert detail["landed"] is False

        # Git/forge truth: no PR for the fixture repo ever merged.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls and not any(p.get("merged") for p in pulls), f"a conflicted PR merged: {pulls}"

    # Bare main is exactly where it started — the conflicted change never landed.
    main_after = _git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after == main_before, "bare main moved despite the armed conflict"
