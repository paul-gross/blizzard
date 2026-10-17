"""A conflict at the default graph's `deliver` node lands ZERO repos (#67).

With the mock forge's `merge_conflict` lever armed, `deliver` finds the PR not cleanly
mergeable: nothing lands, the bounce routes back to `build` (#64), and the route is kept.
Skipped unless `BLIZZARD_E2E=1` with the sibling `blizzard-mock` worktree provisioned.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

import httpx
import pytest

from blizzard.runner.composition import RunnerProcess
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.e2e.fleet_traces import (
    PLANT_DIR_VAR,
    PLANT_LEASE_TOKEN_SCRIPT,
    FleetCollector,
    StepExpect,
    assert_platform_nesting,
    assert_skeleton,
    planted_lease_tokens,
    runner_sweep,
    stashed_route_tokens,
    told_until,
)
from tests.fleet_launchers import REPO, git_bare, mock_bin_dir, run_forge, run_hub, runner_api, runner_config
from tests.fleet_world import FIXTURE_ENV, PUSH_AND_DECLARE_SCRIPT, REPO_NAME, mint_world, scripted_build_prompt
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e delivery-conflict needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_BUILD_SCRIPT = scripted_build_prompt(
    PLANT_LEASE_TOKEN_SCRIPT + "import os, subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    # A command whose request the runner passes through to the hub — the chain a trace should show end to end.
    'subprocess.run(["blizzard", "runner", "work-items", os.environ["BLIZZARD_CHUNK_ID"]], check=True, capture_output=True)\n'
    '(pathlib.Path(repo) / "CONFLICTED.md").write_text("armed conflict\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: a change the armed conflict lever will reject"],\n'
    "    check=True,\n"
    ")\n" + PUSH_AND_DECLARE_SCRIPT
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
                        "failure": {"description": "Failed.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _drive_one_bounce(
    config: RunnerConfig,
    hub: httpx.Client,
    chunk_id: str,
    fenced_env: dict[str, str],
    process: RunnerProcess | None,
) -> str:
    """Tick until the chunk is back at `build` (post-bounce) or reaches a terminal status.

    A conflict never terminates the chunk (#64), so this stops on the first bounce rather
    than driving to `done`, which an always-conflicting repo would never reach.
    """
    prior = dict(os.environ)
    os.environ.update(fenced_env)
    try:
        with runner_api(config, process=process):
            deadline = time.monotonic() + 60.0
            status = "ready"
            while time.monotonic() < deadline:
                LoopWiring.of(config).tick_once(process=process)
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


def _drive_until_rebuilt(
    config: RunnerConfig,
    hub: httpx.Client,
    chunk_id: str,
    fenced_env: dict[str, str],
    process: RunnerProcess | None,
) -> None:
    """Tick until the re-entered build has passed again — its step is closed, so it can be exported."""
    prior = dict(os.environ)
    os.environ.update(fenced_env)
    try:
        with runner_api(config, process=process):
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                LoopWiring.of(config).tick_once(process=process)
                history = hub.get(f"/api/chunks/{chunk_id}").json()["history"]
                if sum(1 for h in history if h["choice_name"] == "pass") >= 2:
                    return
                time.sleep(0.5)
            raise AssertionError("the bounced chunk never re-passed build")
    finally:
        os.environ.clear()
        os.environ.update(prior)


def test_conflict_lands_zero_repos_and_routes_the_bounce_envelope_back_to_build(
    tmp_path: Path, fleet_traces: FleetCollector, subtests: pytest.Subtests
) -> None:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"

    main_before = git_bare(origin_bare, "rev-parse", "main").strip()

    forge_port, hub_port = free_port(), free_port()
    with (
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(tmp_path / "hub", forge_port, hub_port, collector=fleet_traces, platform_spans=True) as hub,
        contextlib.ExitStack() as stack,
    ):
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

        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        planted = tmp_path / "planted"
        planted.mkdir()
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        fenced[PLANT_DIR_VAR] = str(planted)
        process = stack.enter_context(runner_sweep(config, fleet_traces)).process
        status = _drive_one_bounce(config, hub, chunk_id, fenced, process)

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

        # Fleet truth, as the trace backend sees it: the bounced deliver step is routing, not error — it carries a
        # `bounce` event with cause `conflict` and closes back to build, and the re-entered build links to it by
        # `bounce`. The chunk keeps looping against the armed lever, so only the first three steps are read.
        # The hub runs platform spans at a zero root sample ratio: its `hub run step` spans still export, parented
        # on the very `hub exec` span the sweep tells, which proves the inline derivation and the sweep agree.
        with subtests.test(msg="fleet traces"):
            fleet_traces.require()
            _drive_until_rebuilt(config, hub, chunk_id, fenced, process)
            traces = fleet_traces.traces(roots=3, exact=False)
            (bounce,) = [e for e in traces[1].root.events if e.name == "bounce"]
            assert bounce.attributes["blizzard.bounce.cause"] == "conflict"
            assert_skeleton(
                traces,
                [
                    StepExpect("step build", "transitioned", "deliver", children=("queue wait", "claim")),
                    StepExpect(
                        "step deliver",
                        "transitioned",
                        "build",
                        children=("hub exec",),
                        events=("bounce",),
                        link="next",
                        attributes=(("blizzard.bounce.cause", "conflict"),),
                    ),
                    StepExpect("step build", "transitioned", "deliver", link="bounce"),
                ],
            )
            deliver = traces[1]
            (hub_exec,) = [c for c in deliver.children if c.name == "hub exec"]
            # A `hub run step` exports live, its `hub exec` only when its step closes: read the runs of told steps.
            told = told_until([t.root for t in traces])[deliver.root.trace_id]
            run_steps = [
                s
                for s in fleet_traces.platform_spans()
                if s.name == "hub run step" and s.trace_id == deliver.root.trace_id and s.start_ns < told
            ]
            assert run_steps, "the chunk's trace carries no told `hub run step` span"
            # The work trace holds every told deliver step's runs; each hangs on its own step's `hub exec`.
            hub_execs = {c.span_id for trace in traces for c in trace.children if c.name == "hub exec"}
            assert {s.parent_span_id for s in run_steps} <= hub_execs
            assert hub_exec.span_id in {s.parent_span_id for s in run_steps}
            assert all("blizzard.hub.run_step.exit_code" in s.attributes for s in run_steps)

        with subtests.test(msg="platform spans"):
            fleet_traces.require()
            assert_platform_nesting(fleet_traces.spans(roots=3, exact=False), fleet_traces.platform_spans())
            fleet_traces.assert_no_leaks(
                {
                    "lease token": planted_lease_tokens(planted),
                    "route token": stashed_route_tokens(config),
                    "runner bearer": [config.hub_token],
                }
            )

    # Bare main is exactly where it started — the conflicted change never landed.
    main_after = git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after == main_before, "bare main moved despite the armed conflict"
