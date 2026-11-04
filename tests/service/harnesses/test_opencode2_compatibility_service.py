"""Service-tier proof for the OpenCode 2 compatibility diagnostic against a real ``mock-opencode-2 emit`` artifact,
spawned as an out-of-process binary that registers and answers as the managed service under each test's own isolated
home (``bzh:external-cli-fake-is-service-tier``).

The whole diagnostic runs once through the CLI with no lever; each lever then drives the proof row it breaks, through
the production proof, host, and driver core."""

from __future__ import annotations

import json
import stat
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.harness.compatibility import CompatibilityClassification, EvidenceState
from blizzard.runner.harness.opencode_v2.client import ModelReference, ServiceRequestError, ServiceShapeError
from blizzard.runner.harness.opencode_v2.compatibility.host import CommandResult
from blizzard.runner.harness.opencode_v2.compatibility.proof import OpenCodeTwoProof, ProofAbortedError
from blizzard.runner.harness.opencode_v2.compatibility.rows import ProofRow, RowObservation, classify_row
from blizzard.runner.harness.opencode_v2.compatibility.store_migration import StoreMigrationProbe
from blizzard.runner.harness.opencode_v2.compatibility.subprocess_host import SubprocessProofHost, isolated_home
from blizzard.runner.harness.opencode_v2.driver import (
    DriverCore,
    ServiceNotRegisteredError,
    ServiceRestartedError,
    TurnTimeoutError,
)
from blizzard.runner.harness.opencode_v2.registration import read_registration
from blizzard.runner.harness.opencode_v2.transport import ServiceUnreachableError, UrllibServiceTransport
from tests.mock_fleet_support import require_mock_fleet, require_opencode2_cli_surface, service_gate
from tests.support_child_launch import CHILDREN, tool_env

pytestmark = [pytest.mark.service, service_gate]

_ROW_FAILURES = (
    ProofAbortedError,
    ServiceRequestError,
    ServiceShapeError,
    ServiceUnreachableError,
    ServiceRestartedError,
    TurnTimeoutError,
)

MODEL = ModelReference("openai", "gpt-6-luna", "high")
#: How long the proof's attended takeover is left to settle here; the artifact answers well inside it.
TAKEOVER_SETTLE_SECONDS = 8.0

#: A disposable OpenCode 1 that seeds, lists, and exports a 1.x store the way the store-migration row reads one.
_FAKE_OPENCODE1 = """#!/usr/bin/env python3
import json, os, sqlite3, sys
from pathlib import Path
store = Path(os.environ["XDG_DATA_HOME"]) / "opencode" / "opencode.db"
args = sys.argv[1:]
if args == ["--version"]:
    print("1.18.33")
elif args[:1] == ["run"]:
    store.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(store) as db:
        db.execute("create table session (id text primary key, title text)")
        db.execute("create table message (id text primary key, session_id text)")
        db.execute("create table part (id text primary key, message_id text)")
        db.execute("insert into session values ('ses_seeded1', 'seed')")
    print("SEEDED")
elif args == ["session", "list"]:
    with sqlite3.connect(store) as db:
        print("\\n".join(row[0] for row in db.execute("select id from session")))
elif args[:1] == ["export"]:
    print(json.dumps({"info": {"id": args[1]}}))
else:
    sys.exit(2)
"""

#: Every keyword maps 1:1 onto a ``blizzard_mock.harness.opencode2_core.levers.Lever`` member, kept in step by
#: hand (``bzh:opencode-lever-roster-extends-both-sides``), since this repo cannot import ``blizzard_mock``.
_LEVERS = (
    "NEVER_REGISTERED",
    "NEVER_ANSWERS",
    "VERSION_MISMATCH",
    "EVENTS_DROPPED",
    "INTERRUPT_IGNORED",
    "RESTARTS_MID_TURN",
    "DISTORTED_SHAPES",
    "UNKNOWN_PART_TYPES",
    "NO_PRICES",
)


def _fake_binary(tmp_path: Path, *levers: str, version: str = "2.1.4") -> Path:
    """Emit a real ``mock-opencode-2`` artifact baked with ``levers``."""
    assert len(_LEVERS) == 9, "roster size drifted from blizzard-mock's Lever enum — update both sides"
    assert set(levers) <= set(_LEVERS), levers
    mock = require_opencode2_cli_surface(require_mock_fleet())
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "fake-opencode2"
    argv = [str(mock), "emit", "--out", str(out), "--version", version]
    for lever in levers:
        argv += ["--lever", lever]
    proc = subprocess.run(argv, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return out


def _fake_opencode1(tmp_path: Path) -> Path:
    path = tmp_path / "fake-opencode1"
    path.write_text(_FAKE_OPENCODE1)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _home_variables(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }


@dataclass(frozen=True)
class _ShortTakeoverHost:
    """The real host with the attended takeover's settle shortened; every other call passes through."""

    inner: SubprocessProofHost

    def run(self, argv: Sequence[str], *, cwd: Path, timeout: float, home: Path | None = None) -> CommandResult:
        return self.inner.run(argv, cwd=cwd, timeout=timeout, home=home)

    def attend(self, argv: Sequence[str], *, cwd: Path, keystrokes: str, settle_seconds: float) -> None:
        self.inner.attend(
            argv, cwd=cwd, keystrokes=keystrokes, settle_seconds=min(settle_seconds, TAKEOVER_SETTLE_SECONDS)
        )

    def process_alive(self, pid: int) -> bool:
        return self.inner.process_alive(pid)


@dataclass
class _Rig:
    binary: Path
    home: Path
    scratch: Path
    host: _ShortTakeoverHost

    def cli(self, *args: str) -> CommandResult:
        return self.host.run([str(self.binary), *args], cwd=self.scratch, timeout=60)

    def proof(self) -> OpenCodeTwoProof:
        path = self.home / ".local" / "state" / "opencode" / "service.json"
        transport = UrllibServiceTransport()
        return OpenCodeTwoProof(
            binary=str(self.binary),
            model=MODEL,
            scratch=self.scratch,
            host=self.host,
            core_factory=lambda: DriverCore(lambda: read_registration(path), transport),
            migration=StoreMigrationProbe(str(self.binary), None, None, self.scratch.parent / "migration", self.host),
        )


@pytest.fixture
def rig(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[_Rig]:
    levers: tuple[str, ...] = getattr(request, "param", ())
    home = tmp_path / "home"
    scratch = tmp_path / "scratch" / "repo"
    scratch.mkdir(parents=True)
    (scratch / "README.md").write_text("blizzard compatibility proof\n")
    env = isolated_home(tool_env(), home)
    host = _ShortTakeoverHost(SubprocessProofHost(CHILDREN, env))
    host.run(["git", "init", "-q", "."], cwd=scratch, timeout=30)
    rig = _Rig(_fake_binary(tmp_path / "bin", *levers), home, scratch, host)
    assert rig.cli("service", "start").exit_code == 0
    try:
        yield rig
    finally:
        rig.cli("service", "stop")


def _observe(rig: _Rig, row: str) -> RowObservation:
    """One row as the proof records it: a row that raises is a failed result, as in a whole run."""
    proof = rig.proof()
    try:
        method = getattr(proof, f"row_{row}")
        return method(proof.observe_version()) if row == "version" else method()
    except _ROW_FAILURES as exc:
        return RowObservation(ProofRow(row), EvidenceState.FAILED, f"{type(exc).__name__}: {exc}")
    finally:
        proof.cleanup()


def test_the_diagnostic_settles_every_row_against_the_artifact_and_writes_its_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    for name, value in _home_variables(home).items():
        monkeypatch.setenv(name, value)
    binary = _fake_binary(tmp_path / "bin")
    env = isolated_home(tool_env(), home)
    SubprocessProofHost(CHILDREN, env).run([str(binary), "service", "start"], cwd=tmp_path, timeout=60)
    registration = home / ".local/state/opencode/service.json"
    before_restart = str(json.loads(registration.read_text())["password"])
    evidence = tmp_path / "evidence"
    try:
        result = CliRunner().invoke(
            runner_group,
            [
                "opencode",
                "compatibility",
                "--binary",
                str(binary),
                "--model",
                "openai/gpt-6-luna",
                "--variant",
                "high",
                "--evidence-dir",
                str(evidence),
                "--live-provider",
                "--opencode1-binary",
                str(_fake_opencode1(tmp_path)),
                "--opencode1-model",
                "opencode/pulse-alpha-free",
            ],
        )
        after_restart = str(json.loads(registration.read_text())["password"])
    finally:
        SubprocessProofHost(CHILDREN, env).run([str(binary), "service", "stop"], cwd=tmp_path, timeout=60)
    assert result.exit_code == 0, result.output
    assert "compatibility: supported" in result.output
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert manifest["major_version"] == 2
    assert {row["name"]: row["classification"] for row in manifest["rows"]} == {
        row.value: "supported" for row in ProofRow
    }
    store = json.loads((evidence / "opencode1" / "store_migration.json").read_text())
    assert store["signal"] == {"migration.v1-v2": {"phase": "sessions"}} and store["opencode1_lists_after"]
    written = "".join(path.read_text() for path in evidence.rglob("*.json"))
    assert before_restart != after_restart
    assert before_restart not in written and after_restart not in written and str(tmp_path) not in written


def test_never_registered_is_refused_before_any_row_runs(tmp_path: Path) -> None:
    rig_home = tmp_path / "home"
    binary = _fake_binary(tmp_path / "bin", "NEVER_REGISTERED")
    env = isolated_home(tool_env(), rig_home)
    host = _ShortTakeoverHost(SubprocessProofHost(CHILDREN, env))
    host.run([str(binary), "service", "start"], cwd=tmp_path, timeout=60)
    scratch = tmp_path / "repo"
    scratch.mkdir()
    rig = _Rig(binary, rig_home, scratch, host)
    with pytest.raises(ServiceNotRegisteredError):
        rig.proof().run()


@pytest.mark.parametrize("rig", [("NEVER_ANSWERS",)], indirect=True)
def test_never_answers_fails_every_row_that_needs_the_service(rig: _Rig) -> None:
    report = rig.proof().run()
    states = {o.row: o.state for o in report.observations}
    assert states[ProofRow.CALLER_NAMED_SESSION] is EvidenceState.FAILED
    assert states[ProofRow.MANAGED_SERVICE] is EvidenceState.FAILED
    assert report.classification() is CompatibilityClassification.BLOCKING


@pytest.mark.parametrize(
    ("rig", "row", "state", "summary"),
    [
        (("VERSION_MISMATCH",), "version", EvidenceState.FAILED, "the service runs 2.1.5"),
        (("EVENTS_DROPPED",), "events", EvidenceState.ABSENT, "does not tag every session event"),
        (("INTERRUPT_IGNORED",), "interrupt", EvidenceState.FAILED, "interrupt did not stop the tree"),
        (("RESTARTS_MID_TURN",), "session_environment", EvidenceState.FAILED, "ServiceRestartedError"),
        (("DISTORTED_SHAPES",), "prompt_and_completion", EvidenceState.FAILED, "did not answer a list"),
        (("UNKNOWN_PART_TYPES",), "session_environment", EvidenceState.FAILED, "did not behave as the contract"),
        (("NO_PRICES",), "pricing", EvidenceState.ABSENT, "carries no prices"),
    ],
    indirect=["rig"],
)
def test_each_service_lever_breaks_the_row_it_names(rig: _Rig, row: str, state: EvidenceState, summary: str) -> None:
    observation = _observe(rig, row)
    assert observation.state is state, observation.summary
    assert summary in observation.summary, observation.summary


@pytest.mark.parametrize("rig", [()], indirect=True)
def test_the_unlevered_artifact_supports_the_rows_the_levers_break(rig: _Rig) -> None:
    for row in ("version", "events", "interrupt", "session_environment", "prompt_and_completion", "pricing"):
        observation = _observe(rig, row)
        assert classify_row(observation) is CompatibilityClassification.SUPPORTED, (row, observation.summary)
