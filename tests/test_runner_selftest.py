"""``blizzard runner selftest`` — the adapter-drift canary (issue #54).

The job resource (``POST``/``GET /api/selftests``) is exercised over a real app
(TestClient), driving the full check suite against a fake harness binary that mimics
``mock-claude-code``'s CLI surface — spawn honors the pre-assigned session id, the
worker actually edits and commits in the scratch repo, the judgement resume parses to
a ``<Choice>``, an automated follow-up resume returns a pid, and ``resume_command``
composes a sane string. Runs entirely in a throwaway scratch dir: no chunk, lease,
environment binding, or hub call is on this path (the app is built with no store).

The CLI verb (``blizzard runner selftest``) is exercised against a real running
daemon over its unix socket, mirroring ``tests/test_ingest_and_pause_verbs.py``'s
``_serve_local_api`` convention — the pass path prints every check and exits 0, the
fail path (a broken fake binary that ignores the pre-assigned session id) prints the
failing check and exits non-zero, and an unknown harness name is rejected with a 422
naming the one configured harness.

Two more component tests cover the canary's own must-not-hang requirement: a wedged
check (an adapter whose ``spawn`` never returns) must resolve the run ``failed``
within the service's own wall-clock budget rather than leaving it ``running``
forever, and the automated-resume check must reap (kill) the pid it gets back before
the scratch repo it ran against is torn down.
"""

from __future__ import annotations

import json
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from click.testing import CliRunner
from fastapi.testclient import TestClient

from blizzard.foundation.clock import SystemClock
from blizzard.runner.app import build_hosted_app, create_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.listeners import bind_listeners, unlink_socket
from blizzard.runner.selftest.checks import run_selftest_checks
from blizzard.runner.selftest.internal.subprocess_scratch_git import SubprocessScratchGit
from blizzard.runner.selftest.scratch_git import ScratchRepo
from blizzard.runner.selftest.service import SelfTestService

# A fake harness binary mimicking `mock-claude-code`'s CLI surface (mirrors
# `tests/test_runner_harness_adapter.py`'s `_FAKE_HARNESS`), extended to actually
# perform the trivial end-to-end task: on a fresh spawn (no --resume) it edits and
# commits a file in its cwd — the scratch repo the selftest minted — rather than just
# marking that it ran.
_FAKE_HARNESS = """#!/usr/bin/env python3
import json
import subprocess
import sys

args = sys.argv[1:]
session = resume = prompt = None
i = 0
while i < len(args):
    a = args[i]
    if a == "--session-id": session = args[i + 1]; i += 2
    elif a == "--resume": resume = args[i + 1]; i += 2
    elif a == "--output-format": i += 2
    elif a == "--settings": i += 2
    elif a == "--permission-mode": i += 2
    elif a == "--model": i += 2
    elif a in ("-p", "--print"): i += 1
    else: prompt = a; i += 1
sid = resume or session or "auto"
if resume is None:
    with open("SELFTEST.txt", "w") as fh:
        fh.write("ok\\n")
    subprocess.run(["git", "add", "SELFTEST.txt"], check=True)
    subprocess.run(
        ["git", "-c", "user.email=worker@blizzard.local", "-c", "user.name=fake worker",
         "commit", "-q", "-m", "selftest: trivial edit"],
        check=True,
    )
    result = ""
else:
    result = "Assessed. <Choice>pass</Choice>"
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result, "session_id": sid}))
"""

# A drifted harness binding: spawns and exits cleanly (the process mechanics still
# work), but never performs the trivial edit+commit and never emits a parseable
# `<Choice>` on resume — the shape a harness's own CLI update actually breaks (its
# task/verdict conventions), as opposed to a missing binary. Catches drift in the
# end-to-end and verdict-elicitation checks while leaving spawn/resume/resume_command
# — which only exercise process mechanics ClaudeCodeAdapter itself controls — passing.
_BROKEN_HARNESS = """#!/usr/bin/env python3
import json
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "", "session_id": "auto"}))
"""


def _fake_binary(tmp_path: Path, source: str = _FAKE_HARNESS) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "fake-claude"
    script.write_text(source)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(script)


# --------------------------------------------------------------------------- #
# The job resource (component tier, TestClient)
# --------------------------------------------------------------------------- #


def _app_with_harness(tmp_path: Path, binary: str) -> TestClient:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    adapter = ClaudeCodeAdapter(binary=binary)
    return TestClient(create_app(config, harness=adapter))


def _poll_until_done(client: TestClient, selftest_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/selftests/{selftest_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"selftest {selftest_id} never finished within {timeout}s")


@pytest.mark.component
def test_selftest_runs_every_check_against_the_fake_harness_and_passes(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path / "bin")
    client = _app_with_harness(tmp_path / "runner", binary)

    start = client.post("/api/selftests", json={"harness": "claude_code"})
    assert start.status_code == 201, start.text
    run = start.json()
    assert run["status"] == "running"
    assert run["harness"] == "claude_code"

    run = _poll_until_done(client, run["id"])

    assert run["status"] == "passed", run
    names = [c["name"] for c in run["checks"]]
    assert names == [
        "spawn_session_id",
        "end_to_end_edit_commit",
        "verdict_elicitation",
        "automated_resume",
        "resume_command",
    ]
    assert all(c["passed"] for c in run["checks"]), run["checks"]


@pytest.mark.component
def test_selftest_reports_the_failing_checks_on_a_drifted_harness(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path / "bin", source=_BROKEN_HARNESS)
    client = _app_with_harness(tmp_path / "runner", binary)

    start = client.post("/api/selftests", json={"harness": "claude_code"})
    run = _poll_until_done(client, start.json()["id"])

    assert run["status"] == "failed"
    by_name = {c["name"]: c for c in run["checks"]}
    # Process mechanics still work — spawn, resume, and resume_command exercise only
    # what ClaudeCodeAdapter itself controls.
    assert by_name["spawn_session_id"]["passed"] is True
    assert by_name["automated_resume"]["passed"] is True
    assert by_name["resume_command"]["passed"] is True
    # The harness itself drifted: no edit landed, and the resume yields no verdict.
    assert by_name["end_to_end_edit_commit"]["passed"] is False
    assert by_name["verdict_elicitation"]["passed"] is False


@pytest.mark.component
def test_selftest_skips_downstream_checks_when_the_binary_cannot_spawn(tmp_path: Path) -> None:
    # A missing binary (an uninstalled/renamed harness — the sharpest drift case)
    # fails the checks runner before a single subprocess starts.
    missing_binary = str(tmp_path / "no-such-harness")
    client = _app_with_harness(tmp_path / "runner", missing_binary)

    start = client.post("/api/selftests", json={"harness": "claude_code"})
    run = _poll_until_done(client, start.json()["id"])

    assert run["status"] == "failed"
    by_name = {c["name"]: c for c in run["checks"]}
    assert by_name["spawn_session_id"]["passed"] is False
    assert "spawn raised" in by_name["spawn_session_id"]["detail"]
    for name in ("end_to_end_edit_commit", "verdict_elicitation", "automated_resume", "resume_command"):
        assert by_name[name]["passed"] is False
        assert "skipped" in by_name[name]["detail"]


@pytest.mark.component
def test_unknown_harness_is_rejected_naming_the_configured_ones(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path / "bin")
    client = _app_with_harness(tmp_path / "runner", binary)

    resp = client.post("/api/selftests", json={"harness": "codex"})

    assert resp.status_code == 422, resp.text
    assert "codex" in resp.json()["detail"]
    assert "claude_code" in resp.json()["detail"]


@pytest.mark.component
def test_unknown_harness_on_the_store_free_app_names_no_configured_harnesses(tmp_path: Path) -> None:
    # No `harness` bound at all (the store-free app shape) — the registry is empty,
    # so even the one real name is unknown, and the message says so honestly.
    client = TestClient(create_app(RunnerConfig(root=tmp_path, db_url="sqlite://")))

    resp = client.post("/api/selftests", json={"harness": "claude_code"})

    assert resp.status_code == 422, resp.text
    assert "none configured" in resp.json()["detail"]


@pytest.mark.component
def test_get_unknown_selftest_id_is_404(tmp_path: Path) -> None:
    client = _app_with_harness(tmp_path / "runner", _fake_binary(tmp_path / "bin"))

    resp = client.get("/api/selftests/self_does_not_exist")

    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# The canary must not itself hang or leak (findings folded into a7c082f)
# --------------------------------------------------------------------------- #


class _StubScratchGit:
    """Yields a pre-made scratch dir instantly — these tests are about the adapter
    and process seams, not git."""

    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir

    @contextmanager
    def new_scratch_repo(self) -> Iterator[ScratchRepo]:
        yield ScratchRepo(workdir=str(self._workdir))

    def commit_count(self, workdir: str) -> int:
        return 1


class _NeverAliveProcessProbe:
    """A process probe reporting every pid already exited — the poll loops it backs
    (exit-is-done, the resume reap) resolve on their first check."""

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        return False

    def start_time(self, pid: int) -> str | None:
        return None

    def kill(self, pid: int) -> None:
        return None


class _HangingAdapter:
    """A fake coding-harness adapter whose ``spawn`` never returns — the wedged-harness
    shape a real drifted/hung CLI can produce, and the run-budget timeout exists to
    catch."""

    def spawn(self, envelope: object, preamble: object, session_hint: str | None) -> WorkerHandle:
        threading.Event().wait()  # blocks forever
        raise AssertionError("unreachable")

    def resume_with_message(self, environment_id: str, session_id: str, message: str) -> int:
        raise AssertionError("unreachable — spawn never returns")

    def resume_command(self, environment_id: str, session_id: str) -> str:
        raise AssertionError("unreachable — spawn never returns")

    def judge(self, environment_id: str, session_id: str, judgement_prompt: str) -> str:
        raise AssertionError("unreachable — spawn never returns")

    def parse_verdict(self, output: str) -> str | None:
        raise AssertionError("unreachable — spawn never returns")

    def parse_assessment(self, output: str) -> str:
        raise AssertionError("unreachable — spawn never returns")


@pytest.mark.component
def test_selftest_run_that_exceeds_its_budget_fails_loudly_instead_of_hanging(tmp_path: Path) -> None:
    # The wedged check (`spawn` blocks forever) never returns on its own — the fix
    # under test is the service's own wall-clock budget resolving the run anyway.
    service = SelfTestService(
        adapters={"claude_code": _HangingAdapter()},
        scratch_git=_StubScratchGit(tmp_path / "scratch"),
        process=_NeverAliveProcessProbe(),
        clock=SystemClock(),
        run_budget_seconds=0.2,
    )
    client = TestClient(create_app(RunnerConfig(root=tmp_path / "runner", db_url="sqlite://"), selftests=service))

    start = client.post("/api/selftests", json={"harness": "claude_code"})
    assert start.status_code == 201, start.text

    run = _poll_until_done(client, start.json()["id"], timeout=5.0)

    assert run["status"] == "failed", run
    assert run["checks"] == []
    assert run["error"] is not None and "budget" in run["error"]


class _FixedPidAdapter:
    """Spawns and resumes as fast, well-behaved subprocess mechanics honoring fixed
    pids — isolates the automated-resume reap behavior from real process spawning."""

    def __init__(self, spawn_pid: int, resume_pid: int) -> None:
        self.spawn_pid = spawn_pid
        self.resume_pid = resume_pid

    def spawn(self, envelope: object, preamble: object, session_hint: str | None) -> WorkerHandle:
        return WorkerHandle(session_id=session_hint or "sid", pid=self.spawn_pid, process_start_time="spawn-t")

    def resume_with_message(self, environment_id: str, session_id: str, message: str) -> int:
        return self.resume_pid

    def resume_command(self, environment_id: str, session_id: str) -> str:
        return f"cd {environment_id} && fake --resume {session_id}"

    def judge(self, environment_id: str, session_id: str, judgement_prompt: str) -> str:
        return json.dumps({"result": "<Choice>pass</Choice>"})

    def parse_verdict(self, output: str) -> str | None:
        return "pass" if "<Choice>pass</Choice>" in output else None

    def parse_assessment(self, output: str) -> str:
        return ""


class _RecordingProcessProbe:
    """Reports every pid already exited (so the poll loops resolve instantly) while
    recording every ``kill``/``start_time`` call, so a test can assert the resume
    check actually reaped its pid."""

    def __init__(self) -> None:
        self.killed: list[int] = []

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        return False

    def start_time(self, pid: int) -> str | None:
        return "t"

    def kill(self, pid: int) -> None:
        self.killed.append(pid)


@pytest.mark.component
def test_resume_check_reaps_the_resumed_pid_before_the_scratch_repo_is_torn_down(tmp_path: Path) -> None:
    probe = _RecordingProcessProbe()
    adapter = _FixedPidAdapter(spawn_pid=111, resume_pid=222)

    checks = run_selftest_checks(adapter, SubprocessScratchGit(), probe)

    by_name = {c.name: c for c in checks}
    # `_FixedPidAdapter` never actually edits/commits, so `end_to_end_edit_commit`
    # legitimately fails — irrelevant to what this test is proving.
    assert by_name["automated_resume"].passed is True, checks
    # The spawn pid is never touched by the resume check; the resumed pid — the one
    # `resume_with_message` handed back — must be killed before `run_selftest_checks`
    # returns and the scratch repo it ran against is torn down.
    assert probe.killed == [222]


# --------------------------------------------------------------------------- #
# The CLI verb, against a real daemon over its socket
# --------------------------------------------------------------------------- #


def _init_runner_with_binary(tmp_path: Path, binary: str) -> Path:
    root = tmp_path / "runner"
    result = CliRunner().invoke(runner_group, ["init", str(root)], env={"BZ_HARNESS_BINARY": binary})
    assert result.exit_code == 0, result.output
    return root


@contextmanager
def _serve_local_api(root: Path) -> Iterator[Path]:
    """A live runner daemon's local API on its real unix socket.

    Mirrors ``tests/test_ingest_and_pause_verbs.py``'s helper of the same shape: the
    CLI verb is a pure client of this API, so it is driven against a real server over
    a real socket rather than a stubbed transport.
    """
    config = RunnerConfig.load(root, port=0)
    app = build_hosted_app(config)
    sockets = bind_listeners(config)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
    thread.start()
    try:
        _await_socket(config.socket_path)
        yield config.socket_path
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        unlink_socket(config.socket_path)


def _await_socket(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    transport = httpx.HTTPTransport(uds=str(path))
    with httpx.Client(transport=transport, base_url="http://runner") as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
    raise AssertionError(f"runner local API never came up on {path}")


@pytest.mark.component
def test_cli_selftest_prints_every_check_and_exits_zero_on_success(tmp_path: Path) -> None:
    root = _init_runner_with_binary(tmp_path, _fake_binary(tmp_path / "bin"))
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["selftest", "claude_code", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "[PASS] spawn_session_id" in result.output
    assert "[PASS] end_to_end_edit_commit" in result.output
    assert "[PASS] verdict_elicitation" in result.output
    assert "[PASS] automated_resume" in result.output
    assert "[PASS] resume_command" in result.output
    assert "passed for claude_code" in result.output


@pytest.mark.component
def test_cli_selftest_exits_non_zero_and_prints_the_failure_on_a_drifted_harness(tmp_path: Path) -> None:
    root = _init_runner_with_binary(tmp_path, _fake_binary(tmp_path / "bin", source=_BROKEN_HARNESS))
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["selftest", "claude_code", "--dir", str(root)])

    assert result.exit_code != 0
    assert "[FAIL] end_to_end_edit_commit" in result.output
    assert "[FAIL] verdict_elicitation" in result.output
    assert "FAILED for claude_code" in result.output


@pytest.mark.component
def test_cli_selftest_rejects_an_unknown_harness(tmp_path: Path) -> None:
    root = _init_runner_with_binary(tmp_path, _fake_binary(tmp_path / "bin"))
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["selftest", "codex", "--dir", str(root)])

    assert result.exit_code != 0
    assert "codex" in result.output
    assert "claude_code" in result.output
