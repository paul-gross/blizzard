"""Ask/answer park→resume round trip — scenario 4 of the e2e smoke — MVP criterion 7.

The one genuinely new primitive, end to end over the real stack ([ask-answer.md]): a
build worker hits an undecidable choice, runs the **real** ``blizzard runner ask``
(shelled out by the ``mock-claude-code`` façade via ``BLIZZARD_RUNNER_ASK_CMD``, wired
through the runner's spawn env), and **exits**. The chunk parks — its forwarded
question lands at the hub, the reap clock stops, and it derives **waiting_on_human**.
The park is then proven **inert**: several more ticks advance with the dormant lease
never reaped, never re-elicited, and no retry consumed (D-009) — the chunk stays
waiting_on_human on the same single open question.
A human answers at the hub with ``blizzard hub answer``; the runner picks the answer up
on its next tick and **resumes the dormant session** around it — same session — and the
resumed worker commits the change. The chunk then walks build→review→deliver to
**done**, and the mock's persisted session state proves the same session was resumed.

Runs the full live stack like the sibling scenarios, plus the runner's **local API**
(served in a thread) so the real ``blizzard runner ask`` verb has a daemon to POST to,
while the reconciliation loop is driven one synchronous tick at a time for determinism.
Skipped unless ``BLIZZARD_E2E=1`` with the sibling ``blizzard-mock`` worktree provisioned.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from blizzard.runner.app import build_hosted_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import run_single_tick
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _forge,
    _free_port,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _winter_source,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e ask/answer needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# build turn 1: ask an undecidable question and exit (ask-and-exit). The mock's ask()
# shells out to the real `blizzard runner ask` (BLIZZARD_RUNNER_ASK_CMD) before exiting.
_ASK_SCRIPT = 'ask("Which API style should the endpoint use?", ["rest", "graphql"])\n'
# The human's answer, delivered as `blizzard hub answer <qid> "<script>"`. It arrives as
# the resume message (the mock execs it): it makes the real commit the build node owes.
_ANSWER_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed after the human answered\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: resolve the ask and land the change"],\n'
    "    check=True,\n"
    ")\n"
)
# build judgement (elicited on the resumed session after the commit): pass to review.
_JUDGEMENT_SCRIPT = "verdict('pass', 'resumed with the human answer; committed and green')\n"
# review: a fresh cold-eyes pass that produces findings and passes on the first look.
_REVIEW_SCRIPT = "pass\n"
_REVIEW_JUDGEMENT = "verdict('pass', 'cold-eyes review: clean; ready to deliver')\n"


def _graph_yaml() -> str:
    import yaml

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
            "deliver": {"executor": "hub", "mode": "merge-to-main"},
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


@contextlib.contextmanager
def _runner_api(config: RunnerConfig) -> Iterator[None]:
    """Serve the runner's local API in a thread — the daemon `blizzard runner ask` POSTs to.

    The reconciliation loop is still driven synchronously by the test (``run_single_tick``);
    this only stands up the local API surface so the real ask verb has somewhere to land.
    Both share the runner's sqlite store (its busy timeout covers the brief contention).
    """
    app = build_hosted_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="runner-local-api", daemon=True)
    thread.start()
    client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
    try:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                if client.get("/api/health").status_code == 200:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("runner local API did not come up")
        yield
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=10.0)


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


def _tick_n(config: RunnerConfig, fenced: dict[str, str], count: int) -> None:
    """Drive exactly ``count`` full reconciliation ticks (REAP→PULL→FILL→ADVANCE)."""
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(count):
            run_single_tick(config)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _git_bare(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(bare), *args], check=True, capture_output=True, text=True).stdout


def _session_state_path(workspace: Path, session_id: str) -> Path:
    return workspace / ".blizzard-mock-harness" / "sessions" / f"{session_id}.json"


def test_ask_parks_then_answer_resumes_session_to_done(tmp_path: Path) -> None:
    """A worker asks and parks; the human's answer resumes the session and the chunk lands."""
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
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "ask/answer", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        ingested = hub.post(
            "/api/chunks",
            json={"pointers": [{"source": REPO_NAME, "ref": str(issue_number)}]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner (D-103)

        # A free local-API port the worker's `blizzard runner ask` will POST to.
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port(), max_agents=1)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        with _runner_api(config):
            # Phase 1: the worker asks and the chunk parks — derived waiting_on_human.
            status = _tick_until(config, hub, chunk_id, fenced, {"waiting_on_human", "done", "needs_human"}, 90.0)
            assert status == "waiting_on_human", f"chunk did not park (last status {status!r})"

            detail = hub.get(f"/api/chunks/{chunk_id}").json()
            assert detail["questions"], "the parked chunk should surface its open question"
            question = detail["questions"][0]
            question_id = question["question_id"]
            session_id = question["session_id"]
            assert question["options"] == ["rest", "graphql"]

            # The reap clock is stopped while parked ([ask-answer.md] / D-009): drive several
            # more full ticks and prove the park is inert — REAP never reaps the dormant lease,
            # ADVANCE never re-elicits, and no retry is consumed. Observable proof: the chunk
            # stays waiting_on_human and the SAME single question stays open (a consumed retry
            # would re-spawn the worker, which would ask a fresh question or fail the attempt).
            _tick_n(config, fenced, 4)
            still = hub.get(f"/api/chunks/{chunk_id}").json()
            assert still["status"] == "waiting_on_human", f"the park was not inert (status {still['status']!r})"
            open_qs = hub.get("/api/questions").json()
            assert [q["question_id"] for q in open_qs] == [question_id], (
                f"the reap clock was not stopped — the question set changed while parked: {open_qs}"
            )

            # The human answers at the hub via the real `blizzard hub answer` verb.
            answered = subprocess.run(
                [
                    str(Path(sys.executable).parent / "blizzard"),
                    "hub",
                    "answer",
                    question_id,
                    _ANSWER_SCRIPT,
                    "--by",
                    "alice",
                    "--url",
                    f"http://127.0.0.1:{hub_port}",
                ],
                capture_output=True,
                text=True,
            )
            assert answered.returncode == 0, f"hub answer failed:\n{answered.stderr}"

            # Phase 2: the runner resumes the dormant session with the answer and lands.
            status = _tick_until(config, hub, chunk_id, fenced, {"done", "needs_human", "stopped"}, 120.0)
            assert status == "done", f"chunk did not reach done after the answer (last status {status!r})"

        # Fleet truth: the answered question is closed.
        assert hub.get(f"/api/questions/{question_id}").json()["answered"] is True
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

    # The dormant session was resumed around the answer — its persisted state advanced and
    # recorded the resume message carrying the human's answer script (same session).
    state = json.loads(_session_state_path(workspace, session_id).read_text())
    assert any("resolve the ask" in resume for resume in state["resumes"]), (
        f"the resumed session did not record the human's answer: {state['resumes']}"
    )

    # Git truth: the change the resumed worker committed is on the bare origin's main.
    tree = _git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "LANDED.md" in tree.split(), f"landed file not reachable from bare main:\n{tree}"
