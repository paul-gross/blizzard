"""Component proof for the hermetic OpenCode compatibility diagnostic — the narrow slice left
behind once every case that drives the fake OpenCode-shaped CLI as a real out-of-process
subprocess moved to `blizzard:service-test`
(``tests/service/test_opencode_compatibility_service.py``, ``bzh:external-cli-fake-is-service-tier``).

What remains here is:

- Pure domain logic with no subprocess at all (shape classification, evidence-path validation,
  provider-refusal parsing, isolation-root construction).
- A case that resolves an executable path only to prove a pre-flight rejection happens before the
  process would ever be spawned (a missing compactor, a malformed model, an unusable evidence
  destination, an interrupted/failing injected process fake) — "binary-free": a two-line inline
  stub script stands in, since nothing here ever runs it as an OpenCode-shaped CLI.
- A generic subprocess/pty/Landlock-sandbox boundary exercised directly (``sys.executable``, a
  disposable-auth shell wrapper), with no external-tool-shaped CLI in the loop at all
  (`blizzard-context:verification/blizzard/tier-rules.md`'s "third bucket").
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.harness.compatibility import (
    PROBE_ROSTER,
    CompatibilityDiagnostic,
    CompatibilityProbe,
    EvidenceState,
    IncompleteProbeReportError,
    ProbeObservation,
    classify_observation,
)
from blizzard.runner.harness.internal import opencode_process
from blizzard.runner.harness.internal.opencode_attach import LoopbackAttachProxyFactory
from blizzard.runner.harness.internal.opencode_compaction import IOpenCodeCompactor, OpenCodeCompactionResult
from blizzard.runner.harness.internal.opencode_diagnostic import run_opencode_compatibility
from blizzard.runner.harness.internal.opencode_evidence import OpenCodeEvidence, OpenCodeEvidenceError
from blizzard.runner.harness.internal.opencode_facts import provider_refusal
from blizzard.runner.harness.internal.opencode_landlock import landlock_version
from blizzard.runner.harness.internal.opencode_loopback import UrllibLoopbackTransport
from blizzard.runner.harness.internal.opencode_probe import (
    ADMITTED_OPENCODE_VERSIONS,
    BOUNDARY_FAULT_SUMMARY,
    INTERNAL_FAULT_SUMMARY,
    PINNED_OPENCODE_VERSION,
    SHAPE_FAULT_SUMMARY,
    OpenCodeCompatibilityProbe,
)
from blizzard.runner.harness.internal.opencode_process import (
    IOpenCodeProcess,
    OpenCodeProcessError,
    OpenCodeProcessResult,
    OpenCodeStartedProcess,
    SubprocessOpenCodeProcess,
)
from blizzard.runner.harness.internal.opencode_scratch_config import (
    child_env,
    prepare_isolation,
    provision_disposable_auth,
)
from blizzard.runner.harness.internal.opencode_scratch_git import SubprocessOpenCodeScratchGit
from blizzard.runner.harness.internal.opencode_shapes import OpenCodeShapeError, parse_run_jsonl
from blizzard.runner.harness.internal.opencode_transcript import TranscriptExportSample

pytestmark = pytest.mark.component

requires_landlock = pytest.mark.skipif(
    landlock_version() < 3,
    reason="the OpenCode process binding confines children with Landlock ABI 3",
)

MODEL = "openai/gpt-5.6-luna"
VARIANT = "max"


def _stub_binary(tmp_path: Path) -> str:
    """A trivially-executable file — every probe built from this is construction-only: no
    remaining test in this file spawns it as an OpenCode-shaped process, so its content never
    matters."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "fake-opencode"
    path.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(path)


class _UnreachedCompactor(IOpenCodeCompactor):
    """Satisfies the probe constructor's required ``compactor`` seam for the tests below, none of
    which ever reach compaction (they fail, or are diverted by an injected process fake, before
    then)."""

    def compact(
        self,
        *,
        binary: str,
        cwd: Path,
        env: Mapping[str, str],
        session_id: str,
        provider: str,
        model: str,
        capture: Callable[[str, bool], TranscriptExportSample | None],
        record_operation: Callable[[str, Sequence[str]], None],
        record_http_operation: Callable[[str, str, str, int | None], None] | None = None,
    ) -> OpenCodeCompactionResult:
        raise AssertionError("no remaining component test reaches compaction")


def _probe(
    tmp_path: Path,
    *,
    process: IOpenCodeProcess | None = None,
) -> OpenCodeCompatibilityProbe:
    transport = UrllibLoopbackTransport()
    return OpenCodeCompatibilityProbe(
        binary=_stub_binary(tmp_path / "bin"),
        model=MODEL,
        variant=VARIANT,
        scratch_git=SubprocessOpenCodeScratchGit(),
        process=process or SubprocessOpenCodeProcess(),
        compactor=_UnreachedCompactor(),
        transport=transport,
        attach_proxy_factory=LoopbackAttachProxyFactory(transport),
        allow_live_provider=True,
    )


@requires_landlock
def test_compatibility_probe_requires_an_injected_compactor(tmp_path: Path) -> None:
    binary = _stub_binary(tmp_path / "bin")

    with pytest.raises(TypeError, match="compactor"):
        OpenCodeCompatibilityProbe(
            binary=binary,
            model=MODEL,
            variant=VARIANT,
            scratch_git=SubprocessOpenCodeScratchGit(),
            process=SubprocessOpenCodeProcess(),
            transport=UrllibLoopbackTransport(),
            attach_proxy_factory=LoopbackAttachProxyFactory(UrllibLoopbackTransport()),
            allow_live_provider=True,
        )  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("result", "launch_error", "expected_state"),
    [
        (OpenCodeProcessResult(0, '{"children": []}', ""), False, EvidenceState.ABSENT),
        (OpenCodeProcessResult(9, "", ""), False, EvidenceState.FAILED),
        (OpenCodeProcessResult(-1, "", "", timed_out=True), False, EvidenceState.FAILED),
        (OpenCodeProcessResult(0, "not-json", ""), False, EvidenceState.FAILED),
        (None, True, EvidenceState.FAILED),
    ],
)
def test_child_session_operational_outcomes_are_not_silent(
    tmp_path: Path,
    result: OpenCodeProcessResult | None,
    launch_error: bool,
    expected_state: EvidenceState,
) -> None:
    probe = _probe(tmp_path)
    effective = OpenCodeProcessResult(-1, "", "") if launch_error else result
    assert effective is not None

    observation = probe._children_result_observation(effective, "ses_child_probe")

    assert observation.state is expected_state
    if expected_state is EvidenceState.FAILED:
        assert classify_observation(observation).value == "blocking"


def test_child_session_result_must_name_the_probed_session_as_parent(tmp_path: Path) -> None:
    result = OpenCodeProcessResult(0, '{"children": [{"id": "ses_child", "parentID": "ses_other"}]}', "")
    probe = _probe(tmp_path)

    observation = probe._children_result_observation(result, "ses_child_probe")

    assert observation.state is EvidenceState.FAILED
    assert "parent" in observation.summary


class _InterruptingStartedProcess:
    def __init__(self, interruption: BaseException) -> None:
        self.interruption = interruption
        self.stopped = False
        self.cleanup_calls: list[str] = []

    def poll(self) -> int | None:
        return 0 if self.stopped else None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.cleanup_calls.append("wait")
        self.stopped = True
        return 0

    def terminate(self) -> None:
        self.cleanup_calls.append("terminate")
        self.stopped = True

    def kill(self) -> None:
        self.cleanup_calls.append("kill")
        self.stopped = True

    def read_line(self, timeout: float) -> str | None:
        del timeout
        raise self.interruption

    def write_input(self, value: str) -> None:
        del value
        raise self.interruption

    def result(self, timeout: float) -> OpenCodeProcessResult:
        del timeout
        raise self.interruption

    def group_alive(self) -> bool:
        return not self.stopped

    def close_streams(self) -> None:
        self.cleanup_calls.append("close_streams")


class _InterruptingProcess:
    def __init__(self, interruption: BaseException) -> None:
        self.started = _InterruptingStartedProcess(interruption)

    def preflight(self, *, cwd: Path, env: Mapping[str, str]) -> None:
        del cwd, env

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: float,
    ) -> OpenCodeProcessResult:
        del argv, cwd, env, timeout
        return OpenCodeProcessResult(0, "", "")

    def start(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
        del args, kwargs
        return self.started

    def start_capture(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
        del args, kwargs
        return self.started

    def start_interactive(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
        del args, kwargs
        return self.started


def test_fresh_turn_reaps_started_group_when_keyboard_interrupt_arrives(tmp_path: Path) -> None:
    process = _InterruptingProcess(KeyboardInterrupt())
    probe = _probe(tmp_path, process=process)

    with pytest.raises(KeyboardInterrupt):
        probe._fresh_turn(tmp_path, {})

    assert process.started.cleanup_calls[:2] == ["terminate", "wait"]
    assert process.started.stopped is True


def test_process_control_reaps_started_group_when_cancelled(tmp_path: Path) -> None:
    process = _InterruptingProcess(asyncio.CancelledError())
    probe = _probe(tmp_path, process=process)

    with pytest.raises(asyncio.CancelledError):
        probe._process_control_observation(tmp_path, {})

    assert process.started.cleanup_calls[:2] == ["terminate", "wait"]
    assert process.started.stopped is True


@requires_landlock
def test_interactive_start_reaps_process_group_when_setup_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[int] = []
    real_fork = opencode_process.pty.fork

    def recording_fork() -> tuple[int, int]:
        child_pid, master_fd = real_fork()
        spawned.append(child_pid)
        return child_pid, master_fd

    def interrupt_setup(fd: int) -> None:
        del fd
        raise KeyboardInterrupt

    monkeypatch.setattr(opencode_process.pty, "fork", recording_fork)
    monkeypatch.setattr(opencode_process, "_set_interactive_terminal_size", interrupt_setup)

    with pytest.raises(KeyboardInterrupt):
        SubprocessOpenCodeProcess().start_interactive(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env=os.environ,
        )

    assert len(spawned) == 1
    assert spawned[0] > 0
    with pytest.raises(ProcessLookupError):
        os.killpg(spawned[0], 0)


@requires_landlock
def test_interactive_start_gives_the_child_a_real_controlling_terminal(tmp_path: Path) -> None:
    started = SubprocessOpenCodeProcess().start_interactive(
        [
            sys.executable,
            "-c",
            "import os; assert os.isatty(0); assert os.tcgetpgrp(0) == os.getpgrp(); print('tty-ok', flush=True)",
        ],
        cwd=tmp_path,
        env=os.environ,
    )

    result = started.result(5.0)

    assert result.returncode == 0
    assert "tty-ok" in result.stdout
    assert result.process_group_reaped is True


def test_a_provider_refusal_names_only_its_status(tmp_path: Path) -> None:
    del tmp_path
    events = parse_run_jsonl(
        json.dumps(
            {
                "type": "error",
                "sessionID": "ses_x",
                "error": {"name": "APIError", "data": {"message": "sk-secret-in-the-message", "statusCode": 429}},
            }
        )
    )

    summary = provider_refusal(events)

    assert summary == "the provider refused the request with status 429"
    benign = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "ProviderError", "data": {"message": "provider request failed", "statusCode": 503}},
        }
    )
    assert provider_refusal(parse_run_jsonl(benign)) is None


def test_a_command_the_runner_never_started_is_not_reported_as_an_OpenCode_exit(tmp_path: Path) -> None:
    class _UnstartableProcess:
        def preflight(self, *, cwd: Path, env: Mapping[str, str]) -> None:
            del cwd, env

        def run(self, *args: object, **kwargs: object) -> OpenCodeProcessResult:
            raise OpenCodeProcessError("the filesystem boundary could not be applied")

        def start(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
            raise AssertionError("this probe only exercises the captured-command path")

        def start_capture(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
            raise AssertionError("this probe only exercises the captured-command path")

        def start_interactive(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
            raise AssertionError("this probe only exercises the captured-command path")

    probe = _probe(tmp_path, process=_UnstartableProcess())
    export, error = probe._export_session(tmp_path, {}, "ses_x", operation="export")

    assert export is None
    # A status the runner invented says nothing about OpenCode's own exit behavior.
    assert error == INTERNAL_FAULT_SUMMARY
    assert "status -1" not in (error or "")


def test_a_runner_fault_is_not_reported_as_an_OpenCode_shape(tmp_path: Path) -> None:
    class _FailingProcess:
        def preflight(self, *, cwd: Path, env: Mapping[str, str]) -> None:
            del cwd, env
            raise OpenCodeProcessError("no filesystem boundary here")

        def run(self, *args: object, **kwargs: object) -> OpenCodeProcessResult:
            raise AssertionError("the probe ran OpenCode after the boundary failed")

        def start(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
            raise AssertionError("the probe started OpenCode after the boundary failed")

        def start_capture(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
            raise AssertionError("the probe started OpenCode after the boundary failed")

        def start_interactive(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
            raise AssertionError("the probe started OpenCode after the boundary failed")

    probe = _probe(tmp_path, process=_FailingProcess())

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    summaries = {result.summary for result in report.results}
    assert summaries == {BOUNDARY_FAULT_SUMMARY}
    assert SHAPE_FAULT_SUMMARY not in summaries


def test_a_malformed_OpenCode_shape_is_still_attributed_to_OpenCode() -> None:
    probe = OpenCodeCompatibilityProbe.__new__(OpenCodeCompatibilityProbe)

    assert probe._unexpected_error(OpenCodeShapeError("bad field")) == SHAPE_FAULT_SUMMARY
    assert probe._unexpected_error(json.JSONDecodeError("bad", "{", 0)) == SHAPE_FAULT_SUMMARY
    assert probe._unexpected_error(RuntimeError("a runner bug")) == INTERNAL_FAULT_SUMMARY


def test_malformed_model_is_rejected_before_any_diagnostic_side_effect(tmp_path: Path) -> None:
    binary = _stub_binary(tmp_path / "bin")
    evidence = tmp_path / "evidence"

    result = CliRunner().invoke(
        runner_group,
        [
            "opencode",
            "compatibility",
            "--binary",
            binary,
            "--model",
            "not-a-provider-model",
            "--variant",
            VARIANT,
            "--evidence-dir",
            str(evidence),
            "--live-provider",
        ],
    )

    assert result.exit_code != 0
    assert "provider/model" in result.output
    assert not evidence.exists()


def test_unusable_evidence_destination_is_rejected_before_process_start(tmp_path: Path) -> None:
    destination = tmp_path / "not-a-directory"
    destination.write_text("occupied")
    probe = _probe(tmp_path)

    with pytest.raises(OpenCodeEvidenceError, match="not a directory"):
        run_opencode_compatibility(probe, OpenCodeEvidence(destination))

    assert probe.evidence == {}


def test_xdg_data_is_isolated_while_normal_auth_discovery_remains_addressable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_data = tmp_path / "host-data"
    auth_source = host_data / "opencode" / "auth.json"
    auth_source.parent.mkdir(parents=True)
    auth_source.write_bytes(b"synthetic auth fixture")
    monkeypatch.setenv("XDG_DATA_HOME", str(host_data))
    roots = prepare_isolation(tmp_path / "isolated")

    assert not roots.auth_path.exists()
    assert provision_disposable_auth(roots) is True
    assert roots.auth_path.is_file()
    assert not roots.auth_path.is_symlink()
    assert roots.auth_path.read_bytes() == auth_source.read_bytes()
    assert roots.data != host_data


@requires_landlock
def test_model_tool_wrapper_cannot_read_disposable_auth(tmp_path: Path) -> None:
    roots = prepare_isolation(tmp_path / "isolated")
    auth_path = roots.auth_path
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_bytes(b"provider-secret-sentinel")
    auth_path.chmod(0o600)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    scratch.chmod(0o777)
    marker = scratch / "auth-copy"

    result = SubprocessOpenCodeProcess().run(
        [str(roots.model_tool_shell), "-c", f"cat {auth_path} > {marker}"],
        cwd=scratch,
        env=child_env(None, roots),
        timeout=5.0,
    )

    assert result.returncode != 0
    assert marker.read_bytes() == b""

    outside = tmp_path / "outside-write"
    result = SubprocessOpenCodeProcess().run(
        [str(roots.model_tool_shell), "-c", f"printf outside > {outside}"],
        cwd=scratch,
        env=child_env(None, roots),
        timeout=5.0,
    )

    assert result.returncode != 0
    assert not outside.exists()


@requires_landlock
def test_timeout_kills_and_reaps_an_opencode_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-ran"
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import pathlib, sys, time; time.sleep(0.5); pathlib.Path(sys.argv[1]).write_text(\"touched\")', "
        "sys.argv[1]])\n"
        "time.sleep(30)\n"
    )

    result = SubprocessOpenCodeProcess().run(
        [sys.executable, str(launcher), str(marker)],
        cwd=tmp_path,
        env={},
        timeout=0.1,
    )

    assert result.timed_out is True
    time.sleep(0.7)
    assert not marker.exists()


@requires_landlock
def test_process_drains_both_pipes_without_unbounded_capture(tmp_path: Path) -> None:
    result = SubprocessOpenCodeProcess().run(
        [sys.executable, "-c", "import sys; sys.stdout.write('out' * 2000000); sys.stderr.write('err' * 2000000)"],
        cwd=tmp_path,
        env={},
        timeout=5.0,
    )

    assert result.returncode != 0
    assert result.output_truncated is True
    assert len(result.stdout.encode()) <= 4 * 1024 * 1024
    assert len(result.stderr.encode()) <= 4 * 1024 * 1024


@requires_landlock
def test_successful_run_rejects_and_reaps_a_pipe_closing_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-ran"
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import os, subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import os, pathlib, sys, time; os.close(1); os.close(2); time.sleep(0.5); "
        'pathlib.Path(sys.argv[1]).write_text("touched")\', '
        "sys.argv[1]])\n"
        "time.sleep(0.05)\n"
    )

    result = SubprocessOpenCodeProcess().run(
        [sys.executable, str(launcher), str(marker)],
        cwd=tmp_path,
        env={},
        timeout=5.0,
    )

    assert result.returncode != 0
    assert result.process_group_reaped is True
    time.sleep(0.7)
    assert not marker.exists()


class _ContractProbe:
    observed_version = PINNED_OPENCODE_VERSION
    admitted_versions = ADMITTED_OPENCODE_VERSIONS

    def __init__(self, observations: list[ProbeObservation]) -> None:
        self.observations = observations

    def run(self) -> list[ProbeObservation]:
        return self.observations


def _observations() -> list[ProbeObservation]:
    return [ProbeObservation.observed(probe, f"{probe.value} observed") for probe in PROBE_ROSTER]


@pytest.mark.unit
def test_diagnostic_rejects_missing_results_before_evidence_can_be_published() -> None:
    observations = _observations()[:-1]

    with pytest.raises(IncompleteProbeReportError, match="missing probes"):
        CompatibilityDiagnostic(_ContractProbe(observations)).run()


@pytest.mark.unit
def test_diagnostic_keeps_an_ambiguous_result_blocking() -> None:
    observations = _observations()
    observations[0] = ProbeObservation.ambiguous(CompatibilityProbe.FRESH_TURN, "two possible outcomes")

    report = CompatibilityDiagnostic(_ContractProbe(observations)).run()

    assert report.complete is True
    assert report.classification.value == "blocking"
    assert report.results[0].classification.value == "blocking"
    assert report.admissible is False
