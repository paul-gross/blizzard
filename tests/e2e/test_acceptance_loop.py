"""The acceptance loop — the `test_acceptance_loop` scenario of the standing e2e smoke.

One chunk travels ingest -> acquire -> build -> review (scripted PASS) -> deliver ->
landed, asserted at both the bare origin (git truth) and the hub's derived ``done``
status (fleet truth), under both mock harnesses. Self-managed, zero-token, no-network, no in-process shortcuts.
Skipped unless ``BLIZZARD_E2E=1`` and the fixture workspace layout is discoverable."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from blizzard.foundation import cli_spans
from tests.fleet_launchers import (
    REPO,
    drive_until_done,
    git_bare,
    mock_bin_dir,
    run_forge,
    run_hub,
    runner_config,
)
from tests.fleet_traces import (
    PLANT_DIR_VAR,
    SENTINEL_ARTIFACT_BODY,
    FleetCollector,
    InvocationExpect,
    StepExpect,
    assert_chunk_trace,
    assert_invocations,
    assert_platform_nesting,
    assert_skeleton,
    fleet_collector,
    is_fleet,
    planted_lease_tokens,
    runner_sweep,
    whole_chunk_decision_wait,
)
from tests.fleet_world import (
    FIXTURE_ENV,
    JUDGEMENT_SCRIPT,
    PUSH_AND_DECLARE_SCRIPT,
    REPO_NAME,
    REVIEW_JUDGEMENT,
    REVIEW_SCRIPT,
    graph_yaml,
    mint_world,
    scripted_build_prompt,
)
from tests.harness_variants import CLAUDE_CODE, MockHarness, both_mock_harnesses
from tests.support import (
    free_port,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e acceptance loop needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


# The pass-through scenario's distinctive work item — a body + a comment whose exact text
# is asserted on the bare origin's main.
_WORK_ITEM_BODY = "PASSTHROUGH-BODY: the widget flake reproduces under load"
_WORK_ITEM_COMMENT = "PASSTHROUGH-COMMENT: attached a failing repro in the linked gist"

# Build turn: reads the chunk's work item via the real `blizzard runner work-items` verb,
# then commits the fetched body + comment so the pass-through's output lands as git truth.
_WORK_ITEM_BUILD_SCRIPT = scripted_build_prompt(
    "import os, json, subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
    "out = subprocess.run(\n"
    '    ["blizzard", "runner", "work-items", chunk_id],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout\n"
    "item = json.loads(out)['items'][0]\n"
    "payload = item['body'] + '\\n' + '\\n'.join(item['comments']) + '\\n'\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text(payload)\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: land the work item fetched through the pass-through"],\n'
    "    check=True,\n"
    ")\n" + PUSH_AND_DECLARE_SCRIPT
)


# --------------------------------------------------------------------------- #
# The loop


@both_mock_harnesses
def test_acceptance_loop_one_chunk_ingest_to_landed(
    tmp_path: Path, fleet_traces: FleetCollector, subtests: pytest.Subtests, harness: MockHarness
) -> None:
    """One chunk travels the whole lifecycle and derives ``done``."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    # 1. Mint a fresh, disposable fixture world: bare file:// origins + an
    #    empty workspace dir (no winter). `reset` re-mints from clean, so the test is repeatable.
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    assert workspace.is_dir() and origin_bare.is_dir(), "fixture mint did not lay out the expected tree"

    # `mint_world` fenced the tree so the mock harness will run (gated on a marker file + env
    # var); the fence covers every acquired env worktree under it via the engine's ancestor walk.

    forge_port, hub_port = free_port(), free_port()
    with (
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(tmp_path / "hub", forge_port, hub_port, collector=fleet_traces, platform_spans=True) as hub,
    ):
        # Sanity: the forge sees the fixture's bare repo on default branch main.
        repo = forge.get(f"/repos/{REPO}")
        assert repo.status_code == 200, repo.text
        assert repo.json()["default_branch"] == "main"

        # 2. Pre-mint the scripted default graph (reused by name on ingest), then file
        #    an issue on the forge and ingest its pointer -> a `ready` chunk.
        minted = hub.post("/api/graphs", json={"definition_yaml": graph_yaml(harness, probed=True)})
        assert minted.status_code == 201, minted.text

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "land a change", "body": "the acceptance chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]

        ingested = hub.post(
            "/api/chunks",
            json={"tokens": [f"{REPO_NAME}:{issue_number}"]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "not_ready"  # rests not-ready
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"

        # 3. Drive the runner loop one synchronous tick at a time until the chunk lands.
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        planted = tmp_path / "planted"
        planted.mkdir()
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        fenced[PLANT_DIR_VAR] = str(planted)
        with runner_sweep(config, fleet_traces) as sweep:
            sweep.plant()
            status = drive_until_done(config, hub, chunk_id, fenced, process=sweep.process)
            sweep.drain(workers=2)

        # 4a. Fleet truth — the hub's facts derive the chunk done.
        assert status == "done", f"chunk did not reach done (last status {status!r})"

        # 4b. The forge reports the PR merged (delivery's land step ran for real).
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls, "no PR was opened at the forge"
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"
        # The PR traces back to what it delivers: the issue as the forge cross-links it, and the chunk.
        merged_pr = next(p for p in pulls if p.get("merged"))
        assert f"Refs {REPO}#{issue_number}" in (merged_pr.get("body") or ""), merged_pr
        assert f"Chunk: {chunk_id}" in (merged_pr.get("body") or ""), merged_pr

        # 4c. Fleet truth, as the trace backend sees it: build, review and the hub's deliver step under the work
        #    root, each told once, chained by `next` links, the last leading to the reserved terminal.
        with subtests.test(msg="fleet traces"):
            fleet_traces.require()
            assert_skeleton(
                fleet_traces.traces(roots=3),
                [
                    StepExpect("step build", "transitioned", "review", children=("queue wait", "claim")),
                    StepExpect("step review", "transitioned", "deliver", link="next"),
                    StepExpect("step deliver", "transitioned", "done", children=("hub exec",), link="next"),
                ],
            )

        with subtests.test(msg="chunk trace"):
            fleet_traces.require()
            traces = assert_chunk_trace(fleet_traces.spans(roots=3), chunk_id, outcome="done", steps=3)
            assert traces.lifetime.attributes["blizzard.chunk.bounces"] == 0
            assert traces.work.attributes["blizzard.chunk.bounces"] == 0

        with subtests.test(msg="runner traces"):
            fleet_traces.require()
            runner = fleet_traces.runner_spans(roots=3, workers=2)
            expect = InvocationExpect(harness.harness_id, harness.response_model, harness.request_model)
            assert_invocations(runner, {"build": expect, "review": expect})

        with subtests.test(msg="platform spans"):
            fleet_traces.require()
            assert_platform_nesting(fleet_traces.spans(roots=3), fleet_traces.platform_spans())
            fleet_traces.assert_no_leaks(
                {
                    "lease token": planted_lease_tokens(planted),
                    "runner bearer": [config.hub_token],
                    "artifact body": [SENTINEL_ARTIFACT_BODY],
                }
            )

    # 4e. Git truth — the mock harness's file is present on the bare origin's main.
    tree = git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "LANDED.md" in tree.split(), f"landed file not reachable from bare main:\n{tree}"
    # The merge commit carries the same reference, so the trace survives in git alone.
    message = git_bare(origin_bare, "log", "-1", "--format=%B", "main")
    assert f"Refs {REPO}#{issue_number}" in message, f"merge commit names no work item:\n{message}"


# --------------------------------------------------------------------------- #
# Scenario: a tail-sampling collector's decision_wait splits a chunk's trace, or keeps it whole

_HUB_SETTLE_SECONDS = 3
_SWEEP_SECONDS = 1
# The longest chunk this run has: the build, review and delivery each take a few seconds.
_LONGEST_CHUNK_SECONDS = 60
_SHORT_DECISION_WAIT_SECONDS = 1


@pytest.mark.parametrize("setting", ["short", "whole-chunk"])
def test_a_tail_sampling_decision_wait_splits_or_keeps_the_chunk_trace(
    tmp_path: Path, setting: str, subtests: pytest.Subtests
) -> None:
    """A collector that keeps a trace only if it holds a step root decides on a trace's first span, long before
    the roots arrive: a short ``decision_wait`` keeps nothing of the chunk's trace, and one longer than the
    whole chunk keeps roots and platform chain."""
    wait = (
        _SHORT_DECISION_WAIT_SECONDS
        if setting == "short"
        else whole_chunk_decision_wait(
            settle_seconds=_HUB_SETTLE_SECONDS,
            sweep_seconds=_SWEEP_SECONDS,
            longest_chunk_seconds=_LONGEST_CHUNK_SECONDS,
        )
    )
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    workspace = scratch / FIXTURE_ENV / "workspace"
    origins = scratch / FIXTURE_ENV / "origins"

    forge_port, hub_port = free_port(), free_port()
    with (
        fleet_collector(tmp_path / "collector", decision_wait_seconds=wait) as collector,
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(
            tmp_path / "hub",
            forge_port,
            hub_port,
            collector=collector,
            platform_spans=True,
            settle_seconds=_HUB_SETTLE_SECONDS,
        ) as hub,
    ):
        collector.require()
        assert (
            hub.post("/api/graphs", json={"definition_yaml": graph_yaml(CLAUDE_CODE, probed=True)}).status_code == 201
        )
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "land a change", "body": "the tail-sampled chunk"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        planted = tmp_path / "planted"
        planted.mkdir()
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        fenced[PLANT_DIR_VAR] = str(planted)
        with runner_sweep(config, collector, settle_seconds=_HUB_SETTLE_SECONDS, sweep_seconds=_SWEEP_SECONDS) as sweep:
            sweep.plant()
            status = drive_until_done(config, hub, chunk_id, fenced, process=sweep.process)
            assert status == "done", f"chunk did not reach done (last status {status!r})"
            sweep.drain(workers=2)

        # What the sampler was given is the whole trace of every step; what it keeps of it depends on its wait.
        sent = collector.received_spans(roots=3)
        sent_platform = [s for s in sent if not is_fleet(s)]
        assert_platform_nesting([s for s in sent if is_fleet(s)], sent_platform)
        with subtests.test(msg=setting):
            if setting == "short":
                # Decided on the chain's first span, with no root yet, every trace was dropped — and a trace
                # dropped stays dropped, so the late root went with it.
                time.sleep(wait + 2)
                kept = collector.kept_spans()
                assert not [s for s in kept if s.is_step_root], "a short decision_wait still kept a step root"
                assert not [s for s in kept if s.scope == cli_spans.SCOPE_NAME], (
                    "a short decision_wait still kept the worker commands' spans"
                )
            else:
                assert_platform_nesting(collector.spans(roots=3), collector.platform_spans())


# --------------------------------------------------------------------------- #
# Scenario: the work item reaches the build worker through the pass-through (criterion 1)


def _work_item_graph_yaml() -> str:
    """The ``default-delivery`` shape whose build node reads its work item through the proxy."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _WORK_ITEM_BUILD_SCRIPT,
                "judgement": {
                    "prompt": JUDGEMENT_SCRIPT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "review"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": REVIEW_SCRIPT,
                "session": "fresh",
                "produces": ["review-findings"],
                "judgement": {
                    "prompt": REVIEW_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "Passes cold-eyes review.", "to": "deliver"},
                        "fail": {"description": "Blocking issues.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "action": "land",
                "poll_interval": 1,
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                        "failure": {"description": "Failed.", "to": "build"},
                        "inherited-failure": {"description": "The base check stays red.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_build_worker_reads_work_item_through_the_passthrough(tmp_path: Path) -> None:
    """The build worker fetches its issue body + comments through the runner->hub proxy
    and commits the fetched text; asserted reachable from the bare origin's ``main``."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _work_item_graph_yaml()}).status_code == 201

        # File an issue with a distinctive body AND a distinctive comment, then ingest it.
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "pass-through", "body": _WORK_ITEM_BODY})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        commented = forge.post(f"/repos/{REPO}/issues/{issue_number}/comments", json={"body": _WORK_ITEM_COMMENT})
        assert commented.status_code == 201, commented.text

        # Ingest by {source, ref} — the source names the configured binding, the ref is
        # its opaque item token (the issue number).
        ingested = hub.post(
            "/api/chunks",
            json={"tokens": [f"{REPO_NAME}:{issue_number}"]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner

        # Sanity: the hub's own pass-through returns the body + comment, one entry per pointer.
        item = hub.get(f"/api/chunks/{chunk_id}/work-items")
        assert item.status_code == 200, item.text
        entry = item.json()["items"][0]
        assert entry["body"] == _WORK_ITEM_BODY
        assert entry["comments"] == [_WORK_ITEM_COMMENT]

        # Drive the loop — `runner_config` binds a free host/port and `drive_until_done`
        # wraps it in `runner_api`, so the worker's CLI verbs have a daemon to reach.
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        status = drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"

    # Git truth: the body and comment the worker fetched are on the bare origin's main.
    landed = git_bare(origin_bare, "show", "main:LANDED.md")
    assert _WORK_ITEM_BODY in landed, f"the fetched issue body did not reach the worker:\n{landed}"
    assert _WORK_ITEM_COMMENT in landed, f"the fetched issue comment did not reach the worker:\n{landed}"
