"""The PR + CI-watch example graph delivers through the generic path (#67).

The e2e-tier proof that delivery **policy lives in YAML**: the shipped example graph
`hub/graphs/delivery-pr-ci.yaml` differs from the default graph only in its `deliver`
node's `run:` script (`hub/graphs/scripts/land_pr_ci.py` vs `scripts/land_default.py`)
and its poll cadence — yet it expresses a wholly different delivery policy (open a PR
per repo, report the machinery-reserved `pending` outcome (#66) while any PR is not yet
cleanly mergeable, merge once every PR reads `mergeable_state: clean`) through the SAME
generic `executor: hub` primitive, no engine change. This scenario drives that real
`land_pr_ci.py` script end to end against the mock forge:

* the mock forge's `merge_conflict` lever, armed repo-scoped, makes the freshly-opened
  PR read `dirty` — the stand-in for "CI is not yet green" — so the script prints
  `pending`, the executor records a poll-attempt fact, releases the fleet-wide slot,
  and the chunk derives `delivering` (awaiting an external merge) with its route held —
  **nothing merges**;
* clearing the lever is the stand-in for "CI went green": the next poll reads the PR
  `clean`, merges it by pinned SHA, records the `merged/<repo>` marker, prints `landed`,
  and the chunk lands to `done`.

Asserted at both ends over the full live stack (mock forge + mock harness + fixture
workspace + real hub/runner), exactly like the sibling e2e scenarios:

* **fleet truth** — while pending the chunk is not terminal and its detail carries a
  live `pending` view (a recorded poll attempt, no transition); after the lever clears
  it derives `done` with `landed` true;
* **git truth** — bare `main` is unchanged while pending, and carries the build's
  commit exactly once after the land; the forge holds exactly one merged PR.

The shipped `delivery-pr-ci.yaml` authors a production 30s `poll_interval`; this
scenario mints the same `land_pr_ci` script with a brisk 1s `poll_interval` so the
in-process driver converges in seconds (mirroring the crash sweep's own pending
scenario). It is the SAME script — the point proved is the script's policy, run through
the generic executor, not the cadence constant.

Gated exactly like the sibling e2e scenarios — skipped unless `BLIZZARD_E2E=1` and the
sibling `blizzard-mock` worktree + a local winter source are discoverable.

Reproduce it — from the `blizzard` worktree in a provisioned feature env — with::

    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_delivery_pr_ci_e2e.py
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
        reason="e2e delivery-pr-ci needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "PR_CI_LANDED.md").write_text("landed via the PR+CI policy\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: a change the PR+CI policy lands once CI goes green"],\n'
    "    check=True,\n"
    ")\n"
)
_BUILD_JUDGEMENT = "verdict('pass', 'committed the change; checks are green')\n"


def _graph_yaml() -> str:
    """The PR+CI example graph's shape, inlined with a re-poll-every-tick cadence.

    Identical to the shipped `delivery-pr-ci.yaml` except `poll_interval`/`poll_timeout`
    — the `deliver` node names the SAME real `land_pr_ci` script, so this exercises the
    example graph's actual policy, not a stand-in."""
    import yaml

    graph = {
        # Named `default-delivery` (not `-pr-ci`) so `ensure_default` binds THIS graph at
        # ingest: the hub resolves the default by name and takes the newest enabled one,
        # exactly as the sibling conflict scenario does. The PR+CI policy rides its
        # `deliver` node's `run:` script, not the graph name.
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
                "poll_interval": 1,  # a brisk 1s cadence so the scenario converges in seconds
                "poll_timeout": 600,  # never time out — this proves the resume-to-land path, not #64's kick-back
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
                        "failure": {"description": "poll_timeout exceeded; back to build.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _drive_until_pending(config: RunnerConfig, hub: httpx.Client, chunk_id: str, env: dict[str, str]) -> dict:
    """Tick until the chunk records a live pending poll at its hub node (#66).

    Returns the chunk detail the moment `pending` is non-None. A pending poll is the
    node waiting on external merge-ability — the route is held and no transition is
    recorded, so the chunk never reaches a terminal status here."""
    prior = dict(os.environ)
    os.environ.update(env)
    try:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            run_single_tick(config)
            detail = hub.get(f"/api/chunks/{chunk_id}")
            assert detail.status_code == 200, detail.text
            body = detail.json()
            assert body["status"] not in {"done", "stopped", "needs_human"}, (
                f"chunk went terminal ({body['status']!r}) while a PR should be pending — did it merge a dirty PR?"
            )
            if body["pending"] is not None:
                return body
            time.sleep(0.4)
        raise AssertionError("the PR+CI deliver node never recorded a pending poll while the PR read dirty")
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _drive_until_done(config: RunnerConfig, hub: httpx.Client, chunk_id: str, env: dict[str, str]) -> str:
    prior = dict(os.environ)
    os.environ.update(env)
    try:
        deadline = time.monotonic() + 90.0
        status = "delivering"
        while time.monotonic() < deadline:
            run_single_tick(config)
            detail = hub.get(f"/api/chunks/{chunk_id}")
            assert detail.status_code == 200, detail.text
            status = detail.json()["status"]
            if status in {"done", "stopped", "needs_human"}:
                return status
            time.sleep(0.4)
        return status
    finally:
        os.environ.clear()
        os.environ.update(prior)


def test_pr_ci_graph_pends_until_green_then_lands(tmp_path: Path) -> None:
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
        # Arm merge_conflict repo-scoped — the stand-in for "CI is not green yet": the PR
        # the script opens reads dirty, so land_pr_ci prints `pending`, merging nothing.
        armed = forge.post("/_levers/merge_conflict", json={"repo": REPO})
        assert armed.status_code == 200, armed.text

        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "pr-ci", "body": "the PR+CI chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        # Phase 1 — pending: the PR is open but dirty, so nothing merges.
        pending_detail = _drive_until_pending(config, hub, chunk_id, fenced)
        assert pending_detail["status"] == "delivering", pending_detail["status"]
        assert pending_detail["landed"] is False
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls, "the PR+CI script opened no PR while pending"
        assert not any(p.get("merged") for p in pulls), f"a dirty PR merged while pending: {pulls}"
        assert _git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved while pending"

        # Phase 2 — CI goes green: clear the lever, the next poll reads clean and merges.
        cleared = forge.delete("/_levers/merge_conflict", params={"repo": REPO})
        assert cleared.status_code == 200, cleared.text

        status = _drive_until_done(config, hub, chunk_id, fenced)
        assert status == "done", f"the PR+CI chunk did not land after CI went green (last status {status!r})"
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert detail["landed"] is True, "landed did not derive true after the PR merged"

        # Forge truth: exactly one PR, now merged.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        merged = [p for p in pulls if p.get("merged")]
        assert len(merged) == 1, f"expected exactly one merged PR, got {pulls}"

    # Git truth: bare main advanced past its start, carrying the build's commit once.
    main_after = _git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after != main_before, "bare main did not move despite a clean merge"
    landings = [
        ln for ln in _git_bare(origin_bare, "log", "--oneline", "--", "PR_CI_LANDED.md").splitlines() if ln.strip()
    ]
    assert len(landings) == 1, f"PR_CI_LANDED.md landed {len(landings)} times on bare main"
