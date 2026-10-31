"""Ask/answer park→resume round trip — the `test_ask_answer_e2e` scenario of the e2e smoke — MVP criterion 7.

A worker asks and parks (waiting_on_human); a human answers via `blizzard hub question answer` and the runner
resumes the dormant session to done, under both mock harnesses. Skipped unless ``BLIZZARD_E2E=1`` with the
sibling ``blizzard-mock`` worktree provisioned.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from blizzard.runner.composition import RunnerProcess
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.e2e.support import _tick_until
from tests.fleet_launchers import REPO, mock_bin_dir, run_forge, run_hub, runner_api, runner_config
from tests.fleet_traces import (
    PLANT_DIR_VAR,
    PLANT_LEASE_TOKEN_SCRIPT,
    FleetCollector,
    InvocationExpect,
    StepExpect,
    assert_invocations,
    assert_platform_nesting,
    assert_skeleton,
    planted_lease_tokens,
    runner_sweep,
)
from tests.fleet_world import FIXTURE_ENV, PUSH_AND_DECLARE_SCRIPT, REPO_NAME, mint_world, scripted_build_prompt
from tests.harness_variants import MockHarness, both_mock_harnesses
from tests.runner_join import fleet_headers
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e ask/answer needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build turn 1: ask an undecidable question and exit (ask-and-exit).
_ASK_QUESTION = "Which API style should the endpoint use?"
# Before it asks, the worker plants its lease token for the leak scan and calls the hub through the runner.
_ASK_SCRIPT = (
    PLANT_LEASE_TOKEN_SCRIPT
    + "import os, subprocess\n"
    + 'subprocess.run(["blizzard", "runner", "work-items", os.environ["BLIZZARD_CHUNK_ID"]], check=True, capture_output=True)\n'
    + f'ask("{_ASK_QUESTION}", ["rest", "graphql"])\n'
)
# The human's answer, delivered as `blizzard hub question answer <qid> "<script>"`. It
# arrives as the resume message and makes the real commit the build node owes.
_ANSWER_SCRIPT = scripted_build_prompt(
    PLANT_LEASE_TOKEN_SCRIPT + "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed after the human answered\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: resolve the ask and land the change"],\n'
    "    check=True, capture_output=True,\n"
    ")\n" + PUSH_AND_DECLARE_SCRIPT
)
# build judgement (elicited on the resumed session after the commit): pass to review.
_JUDGEMENT_SCRIPT = "verdict('pass', 'resumed with the human answer; committed and green')\n"
# review: a fresh cold-eyes pass that produces findings and passes on the first look.
_REVIEW_SCRIPT = PLANT_LEASE_TOKEN_SCRIPT + "pass\n"
_REVIEW_JUDGEMENT = "verdict('pass', 'cold-eyes review: clean; ready to deliver')\n"


def _graph_yaml(harness: MockHarness) -> str:
    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _ASK_SCRIPT,
                "judgement": {
                    "prompt": _JUDGEMENT_SCRIPT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "review"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": _REVIEW_SCRIPT,
                "session": "fresh",
                "produces": ["review-findings"],
                "judgement": {
                    "prompt": _REVIEW_JUDGEMENT,
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
    return harness.graph_yaml(graph)


def _tick_n(config: RunnerConfig, fenced: dict[str, str], count: int, process: RunnerProcess | None = None) -> None:
    """Drive exactly ``count`` full reconciliation ticks (REAP→PULL→FILL→ADVANCE)."""
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(count):
            LoopWiring.of(config).tick_once(process=process)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _git_bare(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(bare), *args], check=True, capture_output=True, text=True).stdout


def _session_state_path(workspace: Path, session_id: str) -> Path:
    return workspace / ".blizzard-mock-harness" / "sessions" / f"{session_id}.json"


@both_mock_harnesses
def test_ask_parks_then_answer_resumes_session_to_done(
    tmp_path: Path, fleet_traces: FleetCollector, subtests: pytest.Subtests, harness: MockHarness
) -> None:
    """A worker asks and parks; the human's answer resumes the session and the chunk lands."""
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
    with (
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(tmp_path / "hub", forge_port, hub_port, collector=fleet_traces, platform_spans=True) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml(harness)}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "ask/answer", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post(
            "/api/chunks",
            json={"tokens": [f"{REPO_NAME}:{issue_number}"]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner

        # A free local-API port the worker's `blizzard runner ask` will POST to.
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=free_port())
        planted = tmp_path / "planted"
        planted.mkdir()
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        fenced[PLANT_DIR_VAR] = str(planted)

        with runner_sweep(config, fleet_traces) as sweep, runner_api(config, process=sweep.process):
            process = sweep.process
            sweep.plant()
            # The worker asks and the chunk parks — derived waiting_on_human.
            status = _tick_until(
                config, hub, chunk_id, fenced, {"waiting_on_human", "done", "needs_human"}, 90.0, process
            )
            assert status == "waiting_on_human", f"chunk did not park (last status {status!r})"

            detail = hub.get(f"/api/chunks/{chunk_id}").json()
            assert detail["questions"], "the parked chunk should surface its open question"
            question = detail["questions"][0]
            question_id = question["question_id"]
            session_id = question["session_id"]
            assert question["options"] == ["rest", "graphql"]

            # Assert the park is inert: several more ticks leave the chunk waiting_on_human
            # with the same single question open (a consumed retry would re-spawn the worker).
            _tick_n(config, fenced, 4, process)
            still = hub.get(f"/api/chunks/{chunk_id}").json()
            assert still["status"] == "waiting_on_human", f"the park was not inert (status {still['status']!r})"
            open_qs = hub.get("/api/questions").json()
            assert [q["question_id"] for q in open_qs] == [question_id], (
                f"the reap clock was not stopped — the question set changed while parked: {open_qs}"
            )

            # The human answers at the hub via the real `blizzard hub question answer` verb.
            answered = subprocess.run(
                [
                    str(Path(sys.executable).parent / "blizzard"),
                    "hub",
                    "question",
                    "answer",
                    question_id,
                    _ANSWER_SCRIPT,
                    "--by",
                    "alice",
                    "--hub-url",
                    f"http://127.0.0.1:{hub_port}",
                ],
                capture_output=True,
                text=True,
            )
            assert answered.returncode == 0, f"hub question answer failed:\n{answered.stderr}"

            # The runner resumes the dormant session with the answer and lands.
            status = _tick_until(config, hub, chunk_id, fenced, {"done", "needs_human", "stopped"}, 120.0, process)
            assert status == "done", f"chunk did not reach done after the answer (last status {status!r})"
            sweep.drain(workers=2)

        # `delivered` derives from the real runner's `answer.delivered` fact on resume
        # — the one tier where that fact isn't hand-pushed.
        closed = hub.get(f"/api/fleet/questions/{question_id}", headers=fleet_headers(hub)).json()
        assert closed["answered"] is True
        assert closed["delivered"] is True, f"the resume-with-answer left no delivery fact: {closed}"
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

        fleet_traces.await_platform_nesting(roots=3)

        # Fleet truth, as the trace backend sees it: the park and the answer are one build step, carrying an `ask`
        # child that ended answered — the wait never splits the step in two.
        with subtests.test(msg="fleet traces"):
            fleet_traces.require()
            assert_skeleton(
                fleet_traces.traces(roots=3),
                [
                    StepExpect(
                        "step build",
                        "transitioned",
                        "review",
                        children=("queue wait", "claim", "ask"),
                        child_attributes=(("ask", "blizzard.ask.answered", True),),
                    ),
                    StepExpect("step review", "transitioned", "deliver", link="next"),
                    StepExpect("step deliver", "transitioned", "done", children=("hub exec",), link="next"),
                ],
            )

        with subtests.test(msg="runner traces"):
            fleet_traces.require()
            runner = fleet_traces.runner_spans(roots=3, workers=2)
            assert "parked on ask" in [s.name for s in runner], sorted(s.name for s in runner)
            expect = InvocationExpect(harness.harness_id, harness.response_model, harness.request_model)
            assert_invocations(runner, {"build": expect, "review": expect})

        with subtests.test(msg="platform spans"):
            fleet_traces.require()
            assert_platform_nesting(fleet_traces.spans(roots=3), fleet_traces.platform_spans())
            fleet_traces.assert_no_leaks(
                {
                    "lease token": planted_lease_tokens(planted),
                    "runner bearer": [config.hub_token],
                    "ask text": [_ASK_QUESTION],
                }
            )

    # The dormant session was resumed around the answer — its persisted state advanced and
    # recorded the resume message carrying the human's answer script (same session).
    state = json.loads(_session_state_path(workspace, session_id).read_text())
    assert any("resolve the ask" in resume for resume in state["resumes"]), (
        f"the resumed session did not record the human's answer: {state['resumes']}"
    )

    # Git truth: the change the resumed worker committed is on the bare origin's main.
    tree = _git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "LANDED.md" in tree.split(), f"landed file not reachable from bare main:\n{tree}"
