"""Service-tier proof for the OpenCode 2 driver against a real ``mock-opencode-2 emit-engine`` artifact, registered as
the managed service under each test's own isolated home (``bzh:external-cli-fake-is-service-tier``). Each prompt is
the engine's script, run only inside its fence: the variable rides the driver's environment, which the driver applies
to the root, and the service's own, which every child runs under; the marker sits in the session's directory. Most
turns run the real ``blizzard runner opencode drive``; one runs through the adapter and the real launcher."""

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

from blizzard.foundation.clock import SystemClock
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter
from blizzard.runner.harness.opencode_v2.client import ModelReference, OpenCodeServiceClient
from blizzard.runner.harness.opencode_v2.errors import ErrorKind
from blizzard.runner.harness.opencode_v2.registration import read_registration
from blizzard.runner.harness.opencode_v2.request import DriveKind, DriveRequest, write_request
from blizzard.runner.harness.opencode_v2.result import DriverResult, Outcome, parse_result
from blizzard.runner.harness.opencode_v2.service import ServiceKeeper
from blizzard.runner.harness.opencode_v2.shapes import parse_session
from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.mock_fleet_support import require_mock_fleet, service_gate
from tests.runner_fakes import make_envelope
from tests.support_child_launch import CHILDREN, version_probe

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
            stdout=subprocess.PIPE,
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


def test_the_adapter_launches_the_driver_through_the_real_launcher(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_FENCE_VAR, "1")
    monkeypatch.setenv("HOME", str(engine.home))
    for name in _XDG:
        monkeypatch.delenv(name, raising=False)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        process = LinuxProcessProbe()
        worker_env = AllowlistedEnv.of((_FENCE_VAR,))
        adapter = OpenCodeTwoAdapter(
            runner_id="run_1",
            worker_env=worker_env,
            launcher=ProcessLauncher(process, executor=executor, children=CHILDREN),
            keeper=ServiceKeeper(str(engine.binary), worker_env, CHILDREN, UrllibServiceTransport()),
            versions=version_probe(),
            request_dir=engine.requests,
            clock=SystemClock(),
            binary=str(engine.binary),
            driver_command=(_BLIZZARD,),
            model="openai/gpt-6-luna",
        )
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
