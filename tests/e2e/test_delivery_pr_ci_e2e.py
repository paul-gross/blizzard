"""The self-healing PR + CI-watch example graph delivers through the generic path (#67).

The e2e-tier proof that delivery **policy lives in YAML**: the shipped example graph
`hub/graphs/delivery-pr-ci.yaml` differs from the default graph only in its `deliver`
node's `run:` script (`hub/graphs/scripts/land_pr_ci.py` vs `scripts/land_default.py`)
and its poll cadence — yet it expresses a wholly different delivery policy through the
SAME generic `executor: hub` primitive, no engine change. `land_pr_ci` opens a PR per
repo and routes by the PR's live `mergeable_state`, resolving what is mechanical or
transient without ever waking the LLM. This module drives that real script end to end
against the (extended) mock forge, one scenario per route:

* **wait** — the `checks_pending` lever makes the PR read `blocked` (required CI not yet
  green): the script prints `pending`, the executor records a poll attempt and releases
  the fleet-wide slot, the chunk derives `delivering`, **nothing merges**; clearing the
  lever ("CI went green") lets the next poll read `clean`, merge, and land to `done`.
* **self-heal** — the `stale_branch` lever makes the PR read `behind` (base moved, no
  conflict): the script fires `PUT .../update-branch` and pends; the mock advances the
  head and clears the lever, so the next poll reads `clean` and lands — **no LLM**.
  Because that lever clears *only* via update-branch, reaching `done` is itself proof the
  self-heal ran.
* **bounce** — the `merge_conflict` lever makes the PR read `dirty` (a real conflict):
  the script prints `conflict` *immediately* (not a 30-min `poll_timeout` wait), the
  chunk records a `conflict` bounce and routes back to `build`, **nothing lands**.

Asserted over the full live stack (mock forge + mock harness + fixture workspace + real
hub/runner), exactly like the sibling e2e scenarios — fleet truth (pending/bounce/done)
and git truth (bare `main` moves exactly once on a land, never on a pend or a bounce).

The shipped graph authors a production 30s `poll_interval`; these scenarios mint the SAME
`land_pr_ci` script with a brisk 1s cadence so the in-process driver converges in seconds
— the point proved is the script's policy through the generic executor, not the cadence.

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

    Identical to the shipped `delivery-pr-ci.yaml` (including the `conflict` edge the
    self-heal script's `dirty` fast-bounce needs) except `poll_interval`/`poll_timeout` —
    the `deliver` node names the SAME real `land_pr_ci` script, so this exercises the
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
                "poll_timeout": 600,  # never time out — these prove routing, not #64's timeout kick-back
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo's PR is dirty; back to build.", "to": "build"},
                        "failure": {"description": "poll_timeout exceeded; back to build.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _reset_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Reset the mock fixture workspace; skip if the sibling mock/winter aren't present."""
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
    return bin_dir, workspace, origins, origin_bare


def _ingest_and_promote(hub: httpx.Client, forge: httpx.Client) -> str:
    assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": "pr-ci", "body": "the PR+CI chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _drive_until(config: RunnerConfig, hub: httpx.Client, chunk_id: str, env: dict[str, str], predicate, timeout=60.0):
    """Tick until `predicate(detail)` is truthy; return that detail. Raises on timeout."""
    prior = dict(os.environ)
    os.environ.update(env)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run_single_tick(config)
            detail = hub.get(f"/api/chunks/{chunk_id}")
            assert detail.status_code == 200, detail.text
            body = detail.json()
            if predicate(body):
                return body
            time.sleep(0.4)
        raise AssertionError("predicate never became true within the timeout")
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _fenced_env() -> dict[str, str]:
    return {**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"}


def test_pr_ci_pends_on_blocked_then_lands_when_green(tmp_path: Path) -> None:
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = _git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # `blocked` (required CI not green yet) — the correct "not green" wait state.
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        # Phase 1 — pending: the PR is open but blocked, so nothing merges.
        pending = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        assert pending["status"] == "delivering", pending["status"]
        assert pending["landed"] is False
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls and not any(p.get("merged") for p in pulls), f"a blocked PR merged while pending: {pulls}"
        assert _git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved while pending"

        # Phase 2 — CI goes green: clear the lever; the next poll reads clean and merges.
        assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200
        done = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] in {"done", "needs_human"}, timeout=90)
        assert done["status"] == "done", f"did not land after CI went green (status {done['status']!r})"
        assert done["landed"] is True

    main_after = _git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after != main_before, "bare main did not move despite a clean merge"
    landings = [ln for ln in _git_bare(origin_bare, "log", "--oneline", "--", "PR_CI_LANDED.md").splitlines() if ln.strip()]
    assert len(landings) == 1, f"PR_CI_LANDED.md landed {len(landings)} times on bare main"


def test_pr_ci_self_heals_a_behind_branch_and_lands(tmp_path: Path) -> None:
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = _git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # `behind` — base moved, no conflict. The `stale_branch` lever clears ONLY via
        # `PUT .../update-branch`, so reaching `done` proves the script self-healed.
        assert forge.post("/_levers/stale_branch", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        # The behind PR fires update-branch and pends at least once before healing.
        pending = _drive_until(config, hub, chunk_id, fenced, lambda b: b["pending"] is not None)
        assert pending["landed"] is False

        done = _drive_until(config, hub, chunk_id, fenced, lambda b: b["status"] in {"done", "needs_human"}, timeout=90)
        assert done["status"] == "done", f"a behind PR did not self-heal to done (status {done['status']!r})"
        assert done["landed"] is True

        # Forge truth: the once-behind PR is now merged, exactly once.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert len([p for p in pulls if p.get("merged")]) == 1, f"expected one merged PR, got {pulls}"

    main_after = _git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after != main_before, "bare main did not move despite the self-healed land"


def test_pr_ci_bounces_a_dirty_conflict_back_to_build(tmp_path: Path) -> None:
    bin_dir, workspace, origins, origin_bare = _reset_fixture(tmp_path)
    main_before = _git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # `dirty` — a real merge conflict. The script bounces to `build` immediately.
        assert forge.post("/_levers/merge_conflict", json={"repo": REPO}).status_code == 200
        chunk_id = _ingest_and_promote(hub, forge)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = _fenced_env()

        # A conflict routes back to build — the FIRST recorded bounce carries cause `conflict`.
        bounced = _drive_until(config, hub, chunk_id, fenced, lambda b: bool(b.get("bounces")))
        assert bounced["bounces"][0]["cause"] == "conflict", bounced["bounces"]
        assert bounced["landed"] is False

        # Nothing merged; the dirty PR is still open.
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls and not any(p.get("merged") for p in pulls), f"a dirty PR merged: {pulls}"

    assert _git_bare(origin_bare, "rev-parse", "main").strip() == main_before, "bare main moved despite the conflict"
