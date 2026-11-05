"""Service-tier proof for the OpenCode 2 driver and transcript source against a real ``mock-opencode-2 emit-engine``
artifact, registered as the managed service under each test's isolated home (``bzh:external-cli-fake-is-service-tier``).
Each prompt is the engine's script, run only inside its fence: the variable rides the driver's environment and the
service's own, and the marker sits in the session's directory. Most turns run the real ``blizzard runner opencode
drive``; others run through the adapter and the real launcher, its ``attend`` takeover, and its orphan of a killed
driver, and the transcript source reads turns back forward, children included, recovering a killed driver's usage."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
import structlog

from blizzard.foundation.clock import SystemClock
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter
from blizzard.runner.harness.opencode_v2.client import ModelReference, OpenCodeServiceClient, ServiceRequestError
from blizzard.runner.harness.opencode_v2.errors import ErrorKind
from blizzard.runner.harness.opencode_v2.registration import read_registration
from blizzard.runner.harness.opencode_v2.request import FIXED_DENIES, DriveKind, DriveRequest, write_request
from blizzard.runner.harness.opencode_v2.result import DriverResult, Outcome, parse_result
from blizzard.runner.harness.opencode_v2.service import ServiceKeeper
from blizzard.runner.harness.opencode_v2.shapes import parse_message, parse_session
from blizzard.runner.harness.opencode_v2.transcript.source import OpenCodeTwoTranscriptSource
from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport
from blizzard.runner.harness.transcript import TranscriptErrorFactory, TranscriptPosition
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.support.child_launch import CHILDREN, version_probe
from tests.support.mock_fleet import require_mock_fleet, service_gate
from tests.support.runner_fakes import make_envelope

pytestmark = [pytest.mark.service, service_gate]

_FENCE_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"
_FENCE_MARKER = ".blizzard-mock-harness-fence"
_LUNA = ModelReference("openai", "gpt-6-luna", "high")
_BLIZZARD = str(Path(sys.executable).parent / "blizzard")
_XDG = ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")


class _Heartbeats(BaseHTTPRequestHandler):
    beats: ClassVar[list[str]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        type(self).beats.append(self.path)
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@dataclass
class Engine:
    """One engine artifact's service under an isolated home, and the runner the driver heartbeats to."""

    binary: Path
    home: Path
    work: Path
    runner_url: str
    beats: list[str]
    requests: Path = field(init=False)

    def __post_init__(self) -> None:
        self.requests = self.home / "requests"

    def env(self) -> dict[str, str]:
        return {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.home),
            _FENCE_VAR: "1",
            "BLIZZARD_RUNNER_URL": self.runner_url,
            "BLIZZARD_LEASE_ID": "lease_1",
            "BLIZZARD_LEASE_TOKEN": "tok",
        }

    def request(self, script: str, kind: DriveKind = DriveKind.SPAWN, **changes: Any) -> DriveRequest:
        base = DriveRequest(
            kind=kind,
            session_id=f"ses_{os.urandom(8).hex()}",
            directory=str(self.work),
            prompt=script,
            runner_id="run_1",
            lease_id="lease_1",
            autonomy=Autonomy.Normal,
            model=_LUNA,
        )
        return replace(base, **changes)

    def start(self, request: DriveRequest) -> subprocess.Popen[str]:
        path = write_request(self.requests, request)
        return subprocess.Popen(
            [_BLIZZARD, "runner", "opencode", "drive", "--request", str(path)],
            cwd=request.directory,
            env=self.env(),
            stdout=subprocess.PIPE,  # drained-pipe: this spawn reads its own stdout
            stderr=subprocess.PIPE,
            text=True,
        )

    def drive(self, request: DriveRequest, timeout: float = 60) -> DriverResult:
        proc = self.start(request)
        out, err = proc.communicate(timeout=timeout)
        assert proc.returncode == 0, err
        result = parse_result(out)
        assert result is not None, (out, err)
        return result

    def client(self) -> OpenCodeServiceClient:
        registration = read_registration(self.home / ".local" / "state" / "opencode" / "service.json")
        assert registration is not None
        return OpenCodeServiceClient(registration, UrllibServiceTransport())

    def service(self, *args: str) -> subprocess.CompletedProcess[str]:
        # The service's own environment is the one every child session's tools run under.
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), _FENCE_VAR: "1"}
        return subprocess.run([str(self.binary), *args], env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture
def engine_bin(tmp_path: Path) -> Iterator[Path]:
    mock = require_mock_fleet() / "mock-opencode-2"
    if not mock.is_file():
        pytest.skip("the provisioned sibling blizzard-mock has no mock-opencode-2 (run `winter provision <env>`)")
    yield mock


def _engine(tmp_path: Path, mock: Path, *levers: str) -> Iterator[Engine]:
    binary = tmp_path / "bin" / "opencode"
    binary.parent.mkdir(parents=True, exist_ok=True)
    args = [str(mock), "emit-engine", "--out", str(binary), *(a for lever in levers for a in ("--lever", lever))]
    emitted = subprocess.run(args, capture_output=True, text=True)
    assert emitted.returncode == 0, emitted.stderr
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    (work / _FENCE_MARKER).write_text("")
    _Heartbeats.beats = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Heartbeats)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    engine = Engine(binary, home, work, f"http://127.0.0.1:{server.server_port}", _Heartbeats.beats)
    started = engine.service("service", "start")
    assert started.returncode == 0, started.stderr
    try:
        yield engine
    finally:
        engine.service("service", "stop")
        server.shutdown()


@pytest.fixture
def engine(tmp_path: Path, engine_bin: Path) -> Iterator[Engine]:
    yield from _engine(tmp_path, engine_bin)


def test_a_spawned_turn_runs_its_tools_and_reports_text_usage_and_heartbeats(engine: Engine) -> None:
    script = "\n".join(
        [
            "r = shell('echo LEASE=$BLIZZARD_LEASE_ID')",
            "usage(input=120, output=8, reasoning=3, cache_read=40, cost=0.02)",
            "step()",
            "text('saw ' + r.output.strip())",
        ]
    )
    result = engine.drive(engine.request(script))
    assert result.outcome is Outcome.SUCCEEDED
    assert result.text == "saw LEASE=lease_1"
    assert result.steps[0].tokens.input == 120 and result.steps[0].cost == 0.02
    assert result.root_model() == "openai/gpt-6-luna"
    assert engine.beats and set(engine.beats) == {"/api/heartbeat"}


@pytest.mark.parametrize(("autonomy", "status"), [(Autonomy.Normal, "refused"), (Autonomy.Auto, "completed")])
def test_a_permission_request_is_answered_per_autonomy(engine: Engine, autonomy: Autonomy, status: str) -> None:
    script = "r = permission_request('shell', 'touch asked')\ntext(r.status)"
    result = engine.drive(engine.request(script, autonomy=autonomy))
    assert result.text == status
    assert [(d.surface, d.action, d.resources) for d in result.decisions] == [("permission", "shell", ("touch asked",))]


def test_a_form_is_cancelled(engine: Engine) -> None:
    result = engine.drive(engine.request("form('Pick one', [{'key': 'answer', 'type': 'string'}])\ntext('after')"))
    assert result.text == "after"
    assert [(d.surface, d.reply) for d in result.decisions] == [("form", "cancel")]


def test_a_child_is_covered_by_the_rules_runs_without_the_environment_and_its_usage_counts(engine: Engine) -> None:
    child = "r = shell('echo CHILD=${BLIZZARD_LEASE_ID:-none}')\ntext(r.output.strip())"
    script = f"r = subagent({child!r})\ntext('root got ' + r.output.strip())"
    result = engine.drive(engine.request(script))
    assert result.outcome is Outcome.SUCCEEDED
    assert "CHILD=none" in result.text
    assert {step.parent_id is None for step in result.steps} == {True, False}


def test_a_background_child_that_outlives_the_roots_idle_wakes_it_before_the_turn_ends(engine: Engine) -> None:
    child = "shell('sleep 2')\ntext('BACKGROUND-DONE')"
    wake = "text('woken by ' + wake_result())"
    script = f"subagent({child!r}, background=True, wake={wake!r})\ntext('STARTED')"
    result = engine.drive(engine.request(script))
    assert result.text == "woken by BACKGROUND-DONE"


@pytest.mark.parametrize(
    ("script", "kind"),
    [
        ("usage_limit(retry_after_ms=60000)", ErrorKind.USAGE_LIMIT),
        ("overload()", ErrorKind.OVERLOAD),
        ("provider_refusal()", ErrorKind.REFUSAL),
        ("unknown_model()", ErrorKind.CONFIGURATION),
    ],
)
def test_a_provider_failure_is_classified_into_the_result(engine: Engine, script: str, kind: ErrorKind) -> None:
    result = engine.drive(engine.request(script))
    assert result.outcome is Outcome.FAILED
    assert result.error is not None and result.error.kind is kind
    if kind is ErrorKind.USAGE_LIMIT:
        assert result.error.retry_after_ms == 60000


def test_an_unknown_variant_is_refused_before_a_session_exists(engine: Engine) -> None:
    result = engine.drive(engine.request("text('never')", model=replace(_LUNA, variant="ultra")))
    assert result.error is not None and result.error.kind is ErrorKind.CONFIGURATION


def test_a_resume_continues_its_own_session_and_refuses_another_runners(engine: Engine) -> None:
    first = engine.request("text('one')")
    engine.drive(first)
    again = engine.drive(replace(first, kind=DriveKind.NUDGE, prompt="text('two')"))
    assert again.outcome is Outcome.SUCCEEDED and again.text == "two"
    assert [step.message_id for step in again.steps] != []
    foreign = engine.drive(replace(first, kind=DriveKind.RESUME, runner_id="run_other", prompt="text('x')"))
    assert foreign.error is not None and foreign.error.kind is ErrorKind.SESSION_MISSING


def test_sigint_interrupts_the_tree_and_ends_the_turn_interrupted(engine: Engine) -> None:
    child = "hang()"
    request = engine.request(f"subagent({child!r}, background=True)\nhang()")
    proc = engine.start(request)
    time.sleep(3)
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=60)
    result = parse_result(out)
    assert result is not None, err
    assert result.outcome is Outcome.INTERRUPTED
    client = engine.client()
    children = [parse_session(child) for child in client.children(request.session_id)]
    assert children and all(child.outcome == "interrupted" for child in children)
    assert parse_session(client.session(request.session_id)).outcome == "interrupted"


def test_a_lost_event_stream_leaves_polling_to_finish_the_turn(tmp_path: Path, engine_bin: Path) -> None:
    for dropped in _engine(tmp_path, engine_bin, "EVENTS_DROPPED"):
        assert dropped.drive(dropped.request("shell('true')\ntext('polled')")).text == "polled"


def test_a_service_restart_mid_turn_fails_the_turn(tmp_path: Path, engine_bin: Path) -> None:
    for restarting in _engine(tmp_path, engine_bin, "RESTARTS_MID_TURN"):
        result = restarting.drive(restarting.request("shell('sleep 1')\ntext('done')"), timeout=120)
        assert result.outcome is Outcome.FAILED
        assert result.error is not None and result.error.kind is ErrorKind.SERVICE


def _adapter(engine: Engine, executor: ThreadPoolExecutor) -> OpenCodeTwoAdapter:
    worker_env = AllowlistedEnv.of((_FENCE_VAR,))
    return OpenCodeTwoAdapter(
        runner_id="run_1",
        worker_env=worker_env,
        launcher=ProcessLauncher(LinuxProcessProbe(), executor=executor, children=CHILDREN),
        keeper=ServiceKeeper(str(engine.binary), worker_env, CHILDREN, UrllibServiceTransport()),
        versions=version_probe(),
        request_dir=engine.requests,
        clock=SystemClock(),
        binary=str(engine.binary),
        driver_command=(_BLIZZARD,),
        model="openai/gpt-6-luna",
    )


def test_the_adapter_launches_the_driver_through_the_real_launcher(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_FENCE_VAR, "1")
    monkeypatch.setenv("HOME", str(engine.home))
    for name in _XDG:
        monkeypatch.delenv(name, raising=False)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        adapter = _adapter(engine, executor)
        stdout = engine.home / "worker.out"
        preamble = WorkerPreamble(
            environments=[AcquiredEnvironment(environment_id="e1", workdir=str(engine.work))],
            lease_id="lease_1",
            local_api_url=engine.runner_url,
            stdout_path=str(stdout),
        )
        envelope = replace(
            make_envelope("chunk_1", "build", node_id="node_1", choices=[]),
            prompt="text('<Choice>go</Choice> fine')",
        )
        handle = adapter.spawn(envelope, preamble, None, effort="high")
        handle.confirm_durable()
        _wait_exit(handle.pid)
        output = stdout.read_text()
        assert adapter.parse_verdict(output) == "go"
        assert adapter.parse_assessment(output) == "fine"
        sample = adapter.parse_usage(output, "spawn")
        assert sample is not None and sample.model == "openai/gpt-6-luna"
    finally:
        executor.shutdown(wait=True)


def _wait_exit(pid: int, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            finished, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if finished:
            return
        time.sleep(0.1)
    raise AssertionError(f"driver {pid} did not exit")


def test_no_password_reaches_a_result(engine: Engine) -> None:
    registration = json.loads((engine.home / ".local" / "state" / "opencode" / "service.json").read_text())
    proc = engine.start(engine.request("text('x')"))
    out, err = proc.communicate(timeout=60)
    assert registration["password"] not in out + err


def test_takeover_hands_the_session_to_the_operator_and_a_resume_takes_it_back(engine: Engine) -> None:
    child = "hang()"
    request = engine.request(f"subagent({child!r}, background=True)\nhang()")
    worker = engine.start(request)
    time.sleep(3)
    worker.kill()
    worker.communicate(timeout=30)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        command = _adapter(engine, executor).resume_command(
            str(engine.work), request.session_id, model="openai/gpt-6-luna", effort="high", attended=True
        )
    finally:
        executor.shutdown(wait=True)
    client = engine.client()
    client.switch_model(request.session_id, ModelReference("openai", "gpt-6.1-terra", "low"))
    attend = subprocess.Popen(
        ["/bin/sh", "-c", command],
        env=engine.env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert attend.stdin is not None
        attend.stdin.write("text('ATTENDED')\n")
        attend.stdin.flush()
        _wait_for_text(client, request.session_id, "ATTENDED", attend)
        record = client.session(request.session_id)
    finally:
        os.killpg(attend.pid, signal.SIGTERM)
        attend.communicate(timeout=30)
    assert record["permissions"] == []
    children = [parse_session(found) for found in client.children(request.session_id)]
    assert children and all(found.outcome == "interrupted" for found in children)
    switched = [m for m in client.messages(request.session_id) if str(m["type"]).endswith("-switched")]
    assert [m["type"] for m in switched[-3:]] == ["model-switched", "model-switched", "agent-switched"]
    assert parse_session(record).model == _LUNA and record["agent"] == "build"
    resumed = engine.drive(replace(request, kind=DriveKind.RESUME, prompt="text('back')"))
    assert resumed.outcome is Outcome.SUCCEEDED and resumed.text == "back"
    after = client.session(request.session_id)
    assert after["permissions"] == [rule.payload() for rule in FIXED_DENIES]
    assert parse_session(after).model == _LUNA


def _wait_for_text(
    client: OpenCodeServiceClient, session_id: str, text: str, attend: subprocess.Popen[str], timeout: float = 60
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert attend.poll() is None, attend.communicate()[1]
        messages = [parse_message(m) for m in client.messages(session_id)]
        answered = [i for i, m in enumerate(messages) if m.type == "assistant" and m.final_text() == text]
        if answered and any(m.type == "idle" for m in messages[answered[-1] :]):
            return
        time.sleep(0.2)
    raise AssertionError(f"session {session_id} never answered {text!r}")


def test_orphan_stops_the_tree_a_killed_driver_left_running(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(engine.home))
    for name in _XDG:
        monkeypatch.delenv(name, raising=False)
    child = "hang()"
    request = engine.request(f"subagent({child!r}, background=True)\nhang()")
    worker = engine.start(request)
    client = engine.client()
    _wait_for_running(client, request.session_id)
    worker.kill()
    worker.communicate(timeout=30)
    assert request.session_id in client.running_sessions()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        _adapter(engine, executor).orphan(request.session_id)
    finally:
        executor.shutdown(wait=True)
    tree = {request.session_id, *(str(found["id"]) for found in client.children(request.session_id))}
    _wait_for_stopped(client, tree)
    children = [parse_session(found) for found in client.children(request.session_id)]
    assert children and all(found.outcome == "interrupted" for found in children)
    assert parse_session(client.session(request.session_id)).outcome == "interrupted"


def _wait_for_stopped(client: OpenCodeServiceClient, tree: set[str], timeout: float = 30) -> None:
    """The interrupt is a request the service acts on asynchronously, so a tree stops some moments after ``orphan`` returns."""
    deadline = time.monotonic() + timeout
    while client.running_sessions() & tree:
        if time.monotonic() >= deadline:
            raise AssertionError(f"sessions {sorted(client.running_sessions() & tree)} still running after the orphan")
        time.sleep(0.2)


def _wait_for_running(client: OpenCodeServiceClient, session_id: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if session_id in client.running_sessions() and client.children(session_id):
                return
        except ServiceRequestError:
            pass  # the driver has not created the session yet
        time.sleep(0.2)
    raise AssertionError(f"session {session_id} and its child never ran")


# -- the transcript source --------------------------------------------------------------------------------------------


def _transcripts(engine: Engine, runner_id: str = "run_1") -> OpenCodeTwoTranscriptSource:
    path = engine.home / ".local" / "state" / "opencode" / "service.json"
    errors = TranscriptErrorFactory(structlog.get_logger("test"))
    return OpenCodeTwoTranscriptSource(
        lambda: read_registration(path), UrllibServiceTransport(), errors, runner_id=runner_id
    )


def _wait_for_call(client: OpenCodeServiceClient, session_id: str, status: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            messages = [parse_message(m) for m in client.messages(session_id)]
        except ServiceRequestError:
            messages = []  # the driver has not created the session yet
        parts = [p for m in messages for p in m.parts if p.state is not None and p.state.status == status]
        if parts:
            return
        time.sleep(0.1)
    raise AssertionError(f"session {session_id} never had a {status} call")


def test_a_turn_is_read_forward_while_it_runs_and_its_call_patched_once_it_ends(engine: Engine) -> None:
    request = engine.request("shell('sleep 2; echo SLEPT')\ntext('done')")
    worker = engine.start(request)
    _wait_for_call(engine.client(), request.session_id, "running")
    source = _transcripts(engine)
    live = source.turns_since(request.session_id, spawn_cwd=None, since=None)
    call = live.turns[-1].tool
    assert live.available and call is not None and call.name == "shell" and call.output is None
    _, err = worker.communicate(timeout=60)
    assert worker.returncode == 0, err
    after = source.turns_since(request.session_id, spawn_cwd=None, since=live.next_position)
    assert [(late.tool_use_id, "SLEPT" in late.output) for late in after.late_tool_outputs] == [
        (call.tool_use_id, True)
    ]
    assert [turn.text for turn in after.turns if turn.kind == "asst"] == ["done"]
    again = source.turns_since(request.session_id, spawn_cwd=None, since=after.next_position)
    assert again.turns == [] and again.late_tool_outputs == []


def test_a_childs_conversation_is_a_sidechain_of_its_subagent_call(engine: Engine) -> None:
    child = "shell('echo CHILD')\ntext('child done')"
    request = engine.request(f"subagent({child!r}, description='report')\ntext('root done')")
    assert engine.drive(request).outcome is Outcome.SUCCEEDED
    batch = _transcripts(engine).turns_since(request.session_id, spawn_cwd=None, since=None)
    spawns = [turn for turn in batch.turns if turn.tool is not None and turn.tool.name == "subagent"]
    assert spawns and spawns[0].sidechain is not None and spawns[0].tool is not None
    sidechain = spawns[0].sidechain
    assert sidechain.link == "session-id" and "child done" in [turn.text for turn in sidechain.turns]
    assert batch.agent_tool_use_ids == {sidechain.agent_id: spawns[0].tool.tool_use_id}


def test_another_runners_session_is_not_found_and_a_foreign_position_unreadable(engine: Engine) -> None:
    request = engine.request("text('theirs')", runner_id="run_other")
    engine.drive(request)
    assert _transcripts(engine).turns_since(request.session_id, spawn_cwd=None, since=None).reason == "not_found"
    mine = _transcripts(engine, "run_other")
    foreign = TranscriptPosition('{"cursor": {"seen": []}, "linked_children": []}')
    assert mine.turns_since(request.session_id, spawn_cwd=None, since=foreign).reason == "unreadable"


def test_a_killed_drivers_usage_is_recovered_from_the_transcript(engine: Engine) -> None:
    script = "usage(input=120, output=8, reasoning=3, cache_read=40, cost=0)\nstep()\nshell('sleep 60')"
    request = engine.request(script)
    worker = engine.start(request)
    client = engine.client()
    _wait_for_call(client, request.session_id, "running")
    worker.kill()
    worker.communicate(timeout=30)
    try:
        source = _transcripts(engine)
        lines = source.read_raw_lines(request.session_id, spawn_cwd=None)
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            adapter = _adapter(engine, executor)
            sample = adapter.sum_transcript_usage(lines, "spawn")
        finally:
            executor.shutdown(wait=True)
        assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens) == (120, 11, 40)
        assert sample.cost_usd is None and sample.model == "openai/gpt-6-luna"
        assert source.context_tokens(request.session_id, spawn_cwd=None) == 160
    finally:
        client.interrupt(request.session_id)
