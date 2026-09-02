"""Component proof for the hermetic OpenCode compatibility diagnostic.

The executable below is a local fake, not a provider client.  It exercises the real process,
allowlist, disposable-git, parser, report, and evidence bindings without network access.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
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
from blizzard.runner.harness.internal.opencode_compaction import (
    IOpenCodeCompactor,
    OpenCodeCompactionResult,
    SubprocessOpenCodeCompactor,
    compaction_transition_observed,
)
from blizzard.runner.harness.internal.opencode_diagnostic import run_opencode_compatibility
from blizzard.runner.harness.internal.opencode_evidence import OpenCodeEvidence, OpenCodeEvidenceError
from blizzard.runner.harness.internal.opencode_facts import provider_refusal
from blizzard.runner.harness.internal.opencode_landlock import landlock_version
from blizzard.runner.harness.internal.opencode_loopback import UrllibLoopbackTransport
from blizzard.runner.harness.internal.opencode_probe import (
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
from blizzard.runner.harness.internal.opencode_scratch_git import (
    OpenCodeScratchRepo,
    SubprocessOpenCodeScratchGit,
)
from blizzard.runner.harness.internal.opencode_shapes import OpenCodeShapeError, parse_run_jsonl
from blizzard.runner.harness.internal.opencode_transcript import TranscriptExportSample
from tests.support_opencode_binary import MODEL, PROVIDER_SECRET, VARIANT, fake_binary

pytestmark = pytest.mark.component

requires_landlock = pytest.mark.skipif(
    landlock_version() < 3,
    reason="the OpenCode process binding confines children with Landlock ABI 3",
)


class _RecordingScratchGit:
    """Keep the disposed path observable without changing the real scratch binding."""

    def __init__(self) -> None:
        self.inner = SubprocessOpenCodeScratchGit()
        self.path: Path | None = None

    @contextmanager
    def new_scratch_repo(self) -> Iterator[OpenCodeScratchRepo]:
        with self.inner.new_scratch_repo() as repo:
            self.path = repo.workdir
            yield repo

    def has_fresh_commit(self, repo: OpenCodeScratchRepo, relative_path: str, expected: str) -> bool:
        return self.inner.has_fresh_commit(repo, relative_path, expected)


class _FakeCompactor(IOpenCodeCompactor):
    """A seam fake that must make the fake export change before it can pass."""

    def __init__(self, *, effective: bool = True) -> None:
        self.effective = effective

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
        del binary, cwd, session_id, provider, model, record_operation, record_http_operation
        if not self.effective:
            return OpenCodeCompactionResult((), None)
        before = capture("fake_compaction_before", False)
        if before is None:
            return OpenCodeCompactionResult((), "fake compactor could not capture its baseline")
        state_path = Path(env["XDG_STATE_HOME"]) / "fake-opencode-state.json"
        state = {"phase": "done", "compaction_generation": 0}
        with suppress(FileNotFoundError):
            state = json.loads(state_path.read_text())
        state["compaction_generation"] = int(state.get("compaction_generation", 0)) + 1
        state_path.write_text(json.dumps(state))
        after = capture("fake_compaction_after", False)
        if after is None:
            return OpenCodeCompactionResult((), "fake compactor could not capture its result")
        transition = compaction_transition_observed(before.export, after.export)
        return OpenCodeCompactionResult(
            (before, after),
            None if transition else "fake compactor did not cause a compaction transition",
            request_succeeded=True,
            request_status=200,
            transition_observed=transition,
        )


def _probe(
    tmp_path: Path,
    *,
    version: str = PINNED_OPENCODE_VERSION,
    compactor: IOpenCodeCompactor | None = None,
    timeout_seconds: float = 60.0,
    fresh_nonzero: bool = False,
    process_control_no_live_state: bool = False,
    takeover_wrong_directory: bool = False,
    takeover_wrong_session: bool = False,
    takeover_non_sse: bool = False,
    takeover_exit_early: bool = False,
    takeover_idle_sse: bool = False,
    takeover_immediate_eof: bool = False,
    takeover_stream_failure: bool = False,
    takeover_event_gated: bool = False,
    security_command_executes: bool = False,
    mutate_auth: bool = False,
    read_auth: bool = False,
    auth_read_marker: Path | None = None,
    compaction_no_change: bool = False,
    version_touch_path: Path | None = None,
    provider_refusal: bool = False,
    process: IOpenCodeProcess | None = None,
) -> tuple[OpenCodeCompatibilityProbe, _RecordingScratchGit]:
    scratch = _RecordingScratchGit()
    transport = UrllibLoopbackTransport()
    return (
        OpenCodeCompatibilityProbe(
            binary=fake_binary(
                tmp_path / "bin",
                version=version,
                fresh_nonzero=fresh_nonzero,
                process_control_no_live_state=process_control_no_live_state,
                takeover_wrong_directory=takeover_wrong_directory,
                takeover_wrong_session=takeover_wrong_session,
                takeover_non_sse=takeover_non_sse,
                takeover_exit_early=takeover_exit_early,
                takeover_idle_sse=takeover_idle_sse,
                takeover_immediate_eof=takeover_immediate_eof,
                takeover_stream_failure=takeover_stream_failure,
                takeover_event_gated=takeover_event_gated,
                security_command_executes=security_command_executes,
                mutate_auth=mutate_auth,
                read_auth=read_auth,
                auth_read_marker=auth_read_marker,
                compaction_no_change=compaction_no_change,
                version_touch_path=version_touch_path,
                provider_refusal=provider_refusal,
            ),
            model=MODEL,
            variant=VARIANT,
            scratch_git=scratch,
            process=process or SubprocessOpenCodeProcess(),
            compactor=compactor if compactor is not None else _FakeCompactor(),
            transport=transport,
            attach_proxy_factory=LoopbackAttachProxyFactory(transport),
            allow_live_provider=True,
            timeout_seconds=timeout_seconds,
        ),
        scratch,
    )


@requires_landlock
def test_compatibility_probe_requires_an_injected_compactor(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path / "bin")

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


class _ChildrenResultProcess:
    def __init__(self, result: OpenCodeProcessResult | None = None, *, launch_error: bool = False) -> None:
        self._result = result
        self._launch_error = launch_error

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: float,
    ) -> OpenCodeProcessResult:
        del argv, cwd, env, timeout
        if self._launch_error:
            raise OpenCodeProcessError("fake launch failure")
        assert self._result is not None
        return self._result

    def start(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
        del args, kwargs
        raise AssertionError("child probe unexpectedly started a process")

    def start_capture(self, *args: object, **kwargs: object) -> OpenCodeStartedProcess:
        del args, kwargs
        raise AssertionError("child probe unexpectedly started a process")


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
    probe, _ = _probe(tmp_path)
    effective = OpenCodeProcessResult(-1, "", "") if launch_error else result
    assert effective is not None

    observation = probe._children_result_observation(effective, "ses_child_probe")

    assert observation.state is expected_state
    if expected_state is EvidenceState.FAILED:
        assert classify_observation(observation).value == "blocking"


def test_child_session_result_must_name_the_probed_session_as_parent(tmp_path: Path) -> None:
    result = OpenCodeProcessResult(0, '{"children": [{"id": "ses_child", "parentID": "ses_other"}]}', "")
    probe, _ = _probe(tmp_path)

    observation = probe._children_result_observation(result, "ses_child_probe")

    assert observation.state is EvidenceState.FAILED
    assert "parent" in observation.summary


def _stop_compaction_server(child: OpenCodeStartedProcess, *, report_reaped: bool) -> bool:
    child.terminate()
    try:
        child.wait(1.0)
    except Exception:
        child.kill()
        child.wait(1.0)
    return report_reaped


@pytest.mark.parametrize("report_reaped", [True, False])
@requires_landlock
def test_subprocess_compactor_cleanup_result_controls_transcript_admission(tmp_path: Path, report_reaped: bool) -> None:
    compactor = SubprocessOpenCodeCompactor(
        SubprocessOpenCodeProcess(),
        lambda child: _stop_compaction_server(child, report_reaped=report_reaped),
        UrllibLoopbackTransport(),
        timeout_seconds=5.0,
    )
    probe, _ = _probe(tmp_path, compactor=compactor, timeout_seconds=5.0)

    report = CompatibilityDiagnostic(probe).run()

    if report_reaped:
        assert report.admissible is True
    else:
        assert report.admissible is False
        assert report.results[0].probe is CompatibilityProbe.FRESH_TURN
        assert report.results[0].state is EvidenceState.FAILED
        assert report.results[9].probe is CompatibilityProbe.TRANSCRIPT_READ
        assert report.results[9].state is EvidenceState.FAILED


def test_component_fake_cannot_admit_a_preexisting_compaction_marker(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, compactor=_FakeCompactor(effective=False))

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    fresh = next(result for result in report.results if result.probe is CompatibilityProbe.FRESH_TURN)
    assert fresh.state is EvidenceState.FAILED
    assert "attributable compaction transition" in fresh.summary
    transcript = next(result for result in report.results if result.probe is CompatibilityProbe.TRANSCRIPT_READ)
    assert transcript.state is EvidenceState.FAILED


@requires_landlock
def test_real_compactor_requires_a_new_transition_after_successful_summarize(tmp_path: Path) -> None:
    process = SubprocessOpenCodeProcess()
    compactor = SubprocessOpenCodeCompactor(
        process,
        lambda child: _stop_compaction_server(child, report_reaped=True),
        UrllibLoopbackTransport(),
        timeout_seconds=5.0,
    )
    probe, _ = _probe(tmp_path, compactor=compactor, compaction_no_change=True, timeout_seconds=5.0)

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    fresh = next(result for result in report.results if result.probe is CompatibilityProbe.FRESH_TURN)
    assert fresh.state is EvidenceState.FAILED
    assert "new compaction transition" in fresh.summary
    assert probe.evidence["compaction"] == {  # type: ignore[comparison-overlap]
        "request_succeeded": True,
        "request_status": 200,
        "transition_observed": False,
        "effective": False,
    }
    summarize = next(item for item in probe.evidence["operations"] if item["operation"] == "compaction_summarize")  # type: ignore[index]
    assert summarize["http"] == {"method": "POST", "path": "/session/ses_fake/summarize", "status": 200}  # type: ignore[index]


def _cli_args(binary: str, evidence: Path) -> list[str]:
    return [
        "opencode",
        "compatibility",
        "--binary",
        binary,
        "--model",
        MODEL,
        "--variant",
        VARIANT,
        "--evidence-dir",
        str(evidence),
    ]


def _environment_without_git_controls() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def test_fake_binary_runs_every_probe_in_disposable_git_and_retains_sanitized_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", PROVIDER_SECRET)
    monkeypatch.setenv("BZ_HUB_TOKEN", "hub-token-sentinel")
    host_xdg = tmp_path / "host-xdg"
    for name in ("config", "data", "state", "cache"):
        (host_xdg / name).mkdir(parents=True)
        monkeypatch.setenv(f"XDG_{name.upper()}_HOME", str(host_xdg / name))

    caller_repo = tmp_path / "caller-repo"
    subprocess.run(
        ["git", "init", "-q", str(caller_repo)],
        check=True,
        env=_environment_without_git_controls(),
    )
    hook_marker = tmp_path / "caller-hook-ran"
    hook = caller_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\nprintf touched > {hook_marker}\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    global_config = tmp_path / "caller.gitconfig"
    global_config.write_text(f"[core]\n\thooksPath = {hook.parent}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_DIR", str(caller_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(caller_repo))
    monkeypatch.setenv("GIT_INDEX_FILE", str(caller_repo / "caller.index"))

    probe, scratch = _probe(tmp_path)
    evidence = tmp_path / "evidence"

    report = run_opencode_compatibility(probe, OpenCodeEvidence(evidence, secrets=probe.secret_values))

    assert report.observed_version == PINNED_OPENCODE_VERSION
    assert report.complete is True
    assert report.admissible is True
    assert tuple(result.probe for result in report.results) == PROBE_ROSTER
    assert {result.probe for result in report.results if result.state is EvidenceState.ABSENT} == {
        CompatibilityProbe.ROOT_HOOK,
        CompatibilityProbe.CHILD_SESSIONS,
    }
    assert scratch.path is not None
    assert not scratch.path.exists()
    assert not (tmp_path / "compatibility-proof.txt").exists()

    report_text = (evidence / "report.json").read_text()
    runtime_text = (evidence / "runtime.json").read_text()
    assert PROVIDER_SECRET not in report_text
    assert "hub-token-sentinel" not in report_text
    assert PROVIDER_SECRET not in runtime_text
    assert "hub-token-sentinel" not in runtime_text
    assert str(scratch.path) not in runtime_text
    assert "blizzard-opencode-isolation-" not in runtime_text
    runtime = json.loads(runtime_text)
    assert "OPENAI_API_KEY" not in runtime["environment_keys"]
    assert "BZ_HUB_TOKEN" not in runtime["environment_keys"]
    assert "GIT_DIR" not in runtime["environment_keys"]
    assert "GIT_WORK_TREE" not in runtime["environment_keys"]
    assert "OPENCODE_CONFIG_CONTENT" in runtime["environment_keys"]
    assert runtime["config"]["outside_project"] is True
    assert str(caller_repo) not in runtime_text
    version_operation = next(item for item in runtime["operations"] if item["operation"] == "version")
    assert version_operation["argv"][0] == "<binary>"
    assert version_operation["cwd"] == "<workdir>"
    assert str(Path.cwd()) not in runtime_text
    assert probe.binary not in runtime_text
    assert not hook_marker.exists()
    assert not (caller_repo / "caller.index").exists()
    assert runtime["transcript"]["during_export"] is True
    assert runtime["transcript"]["after_export"] is True
    assert runtime["transcript"]["pending_to_completed"] is True
    assert runtime["transcript"]["compaction_pruned"] is True
    assert runtime["transcript"]["repeated_live_exports"] is True
    assert runtime["transcript"]["repeated_after_exports"] is True
    assert runtime["transcript"]["retained_history_not_replayed"] is True
    assert runtime["transcript"]["appended_after_compaction"]
    assert runtime["xdg"]["auth_discovery"]
    assert runtime["xdg"]["auto_update"] == "disabled"
    assert str(host_xdg) not in runtime_text
    assert not re.search(r"\b(?:ses|msg|prt|call)_[A-Za-z0-9]+\b", runtime_text)
    model_variant = next(result for result in report.results if result.probe is CompatibilityProbe.MODEL_VARIANT)
    judgement = next(result for result in report.results if result.probe is CompatibilityProbe.JUDGEMENT)
    assert model_variant.state is EvidenceState.OBSERVED
    assert judgement.state is EvidenceState.OBSERVED
    permission_operation = next(item for item in runtime["operations"] if item["operation"] == "permission")
    assert permission_operation["argv"][1] == "run"
    assert permission_operation["argv"][permission_operation["argv"].index("--agent") + 1] == "compatibility"
    assert "permission-probe" in permission_operation["argv"][-1]
    assert permission_operation["output_retained"] is False
    fresh_operation = next(item for item in runtime["operations"] if item["operation"] == "fresh")
    assert fresh_operation["argv"][fresh_operation["argv"].index("--agent") + 1] == "compatibility-tools"
    security_denial = next(item for item in runtime["operations"] if item["operation"] == "permission_security_denial")
    assert security_denial["argv"][security_denial["argv"].index("--agent") + 1] == "compatibility"
    assert any(item["operation"] == "permission_boundary_1" for item in runtime["operations"])
    assert any(item["operation"] == "permission_boundary_2" for item in runtime["operations"])
    resume_operation = next(item for item in runtime["operations"] if item["operation"] == "resume")
    assert "--session" in resume_operation["argv"]
    assert resume_operation["argv"][resume_operation["argv"].index("--model") + 1] == MODEL
    assert resume_operation["argv"][resume_operation["argv"].index("--variant") + 1] == VARIANT
    takeover_operation = next(item for item in runtime["operations"] if item["operation"] == "takeover_attach")
    assert takeover_operation["argv"][0:2] == ["<binary>", "attach"]
    assert takeover_operation["argv"][takeover_operation["argv"].index("--session") + 1] == "<session-1>"
    assert takeover_operation["argv"][takeover_operation["argv"].index("--dir") + 1] == "<scratch>"
    assert "--no-replay" in takeover_operation["argv"]
    takeover_http = [item for item in runtime["operations"] if item["operation"].startswith("takeover_attach_")]
    assert {item["http"]["path"] for item in takeover_http} >= {"/global/event", "/session/<session-1>"}
    takeover_trigger = next(item for item in runtime["operations"] if item["operation"] == "takeover_event_trigger")
    assert takeover_trigger["http"] == {"method": "POST", "path": "/session", "status": 200}
    children_http = next(item for item in runtime["operations"] if item["operation"] == "children")
    assert children_http["http"] == {
        "method": "GET",
        "path": "/session/<session-1>/children",
        "status": 200,
    }
    assert runtime["process_control"]["live_state_observed"] is True
    assert runtime["takeover"]["continuation_sent"] is True
    assert runtime["takeover"]["continuation_observed"] is True
    assert runtime["compaction"]["effective"] is True
    assert json.loads(report_text)["classification"] == "degraded"


def test_version_preflight_uses_disposable_cwd_and_isolated_child_scopes(tmp_path: Path) -> None:
    caller_marker = tmp_path / "caller-version-touch"
    probe, scratch = _probe(tmp_path, version_touch_path=caller_marker)

    report = CompatibilityDiagnostic(probe).run()

    assert report.observed_version == PINNED_OPENCODE_VERSION
    assert report.admissible is True
    assert not caller_marker.exists()
    preflight = probe.evidence["preflight"]
    assert isinstance(preflight, dict)
    preflight_cwd = Path(preflight["cwd"])
    assert preflight_cwd != Path.cwd()
    assert preflight_cwd.name == "preflight-cwd"
    version_operation = probe.evidence["operations"][0]  # type: ignore[index]
    assert version_operation["cwd"] == str(preflight_cwd)  # type: ignore[index]
    assert set(preflight["environment_keys"]) >= {
        "OPENCODE_CONFIG_DIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
    }
    assert scratch.path is not None and not scratch.path.exists()


def test_fresh_turn_nonzero_exit_blocks_valid_prior_shapes(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    probe, _ = _probe(tmp_path, fresh_nonzero=True)

    report = run_opencode_compatibility(probe, OpenCodeEvidence(evidence, secrets=probe.secret_values))

    assert report.admissible is False
    fresh = next(result for result in report.results if result.probe is CompatibilityProbe.FRESH_TURN)
    assert fresh.state is EvidenceState.FAILED
    assert "exited with status 7" in fresh.summary
    usage = next(result for result in report.results if result.probe is CompatibilityProbe.USAGE_COST)
    assert usage.state is EvidenceState.FAILED


def test_process_control_requires_the_requested_tool_to_be_live(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, process_control_no_live_state=True, timeout_seconds=1.5)

    report = CompatibilityDiagnostic(probe).run()

    process_control = next(result for result in report.results if result.probe is CompatibilityProbe.PROCESS_CONTROL)
    assert process_control.state is EvidenceState.FAILED
    assert "live state" in process_control.summary
    assert probe.evidence["process_control"]["live_state_observed"] is False  # type: ignore[index]


def test_fresh_turn_reaps_started_group_when_keyboard_interrupt_arrives(tmp_path: Path) -> None:
    process = _InterruptingProcess(KeyboardInterrupt())
    probe, _ = _probe(tmp_path, process=process)

    with pytest.raises(KeyboardInterrupt):
        probe._fresh_turn(tmp_path, {})

    assert process.started.cleanup_calls[:2] == ["terminate", "wait"]
    assert process.started.stopped is True


def test_process_control_reaps_started_group_when_cancelled(tmp_path: Path) -> None:
    process = _InterruptingProcess(asyncio.CancelledError())
    probe, _ = _probe(tmp_path, process=process)

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


def test_takeover_requires_recorded_session_workdir_signal(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, takeover_wrong_directory=True)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.FAILED
    assert "scratch workdir" in takeover.summary


def test_takeover_requires_the_exported_session_identity(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, takeover_wrong_session=True)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.FAILED
    assert "identity" in takeover.summary


def test_takeover_rejects_a_non_sse_upstream_response(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, takeover_non_sse=True, timeout_seconds=1.5)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.FAILED
    assert "validated upstream SSE" in takeover.summary


@pytest.mark.parametrize(
    ("takeover_immediate_eof", "takeover_stream_failure", "summary_fragment"),
    [
        (True, False, "validated upstream SSE"),
        (False, True, "successful event stream"),
    ],
)
def test_takeover_rejects_an_immediate_upstream_eof_or_failure(
    tmp_path: Path,
    takeover_immediate_eof: bool,
    takeover_stream_failure: bool,
    summary_fragment: str,
) -> None:
    probe, _ = _probe(
        tmp_path,
        timeout_seconds=1.5,
        takeover_immediate_eof=takeover_immediate_eof,
        takeover_stream_failure=takeover_stream_failure,
    )

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.FAILED
    assert summary_fragment in takeover.summary


def test_takeover_accepts_an_idle_sse_handshake_and_preserves_stream_headers(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, takeover_idle_sse=True, timeout_seconds=1.5)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.OBSERVED
    assert probe.evidence["takeover"]["event_stream_valid"] is True  # type: ignore[index]
    assert probe.evidence["takeover"]["event_stream_bytes"] == 0  # type: ignore[index]


def test_takeover_triggers_local_activity_to_complete_a_gated_upstream_handshake(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, takeover_event_gated=True, timeout_seconds=2.0)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.OBSERVED
    trigger = next(item for item in probe.evidence["operations"] if item["operation"] == "takeover_event_trigger")  # type: ignore[index]
    assert trigger["http"] == {"method": "POST", "path": "/session", "status": 200}  # type: ignore[index]


def test_takeover_requires_client_liveness_after_the_real_sse_handshake(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, takeover_exit_early=True, timeout_seconds=1.5)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.FAILED
    assert "remain alive" in takeover.summary


def test_permission_probe_requires_an_explicit_denied_tool_call_not_model_text(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path)

    report = CompatibilityDiagnostic(probe).run()

    permission = next(result for result in report.results if result.probe is CompatibilityProbe.PERMISSION)
    assert permission.state is EvidenceState.OBSERVED
    assert permission.classification.value == "supported"


def test_permission_probe_blocks_when_a_security_command_executes(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, security_command_executes=True)

    report = CompatibilityDiagnostic(probe).run()

    permission = next(result for result in report.results if result.probe is CompatibilityProbe.PERMISSION)
    assert permission.state is EvidenceState.FAILED
    assert "explicitly denied" in permission.summary


@requires_landlock
def test_effective_config_without_the_runner_shell_is_blocking(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    binary = fake_binary(tmp_path / "bin", drop_config_shell=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "configuration_isolation: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    configuration = next(probe for probe in report["probes"] if probe["name"] == "configuration_isolation")
    assert configuration["summary"] == "OpenCode did not resolve the runner-owned model-tool shell"


@requires_landlock
def test_effective_config_without_the_runner_compaction_bound_is_blocking(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    binary = fake_binary(tmp_path / "bin", drop_config_compaction=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "configuration_isolation: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    configuration = next(probe for probe in report["probes"] if probe["name"] == "configuration_isolation")
    assert configuration["summary"] == "OpenCode did not resolve the runner-owned compaction tail bound"


@requires_landlock
def test_a_provider_refusal_is_not_reported_as_an_OpenCode_timeout(tmp_path: Path) -> None:
    probe, _ = _probe(tmp_path, provider_refusal=True, timeout_seconds=3.0)

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    # The provider answered before OpenCode could act, so no probe observed a contract at all.
    assert {result.state for result in report.results} == {EvidenceState.AMBIGUOUS}
    assert {result.summary for result in report.results} == {"the provider refused the request with status 429"}


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

    probe, _ = _probe(tmp_path, process=_UnstartableProcess())
    export, error = probe._export_session(tmp_path, {}, "ses_x", operation="export")

    assert export is None
    # A status the runner invented says nothing about OpenCode's own exit behavior.
    assert error == INTERNAL_FAULT_SUMMARY
    assert "status -1" not in (error or "")


@requires_landlock
def test_disposable_scratch_repository_is_never_reachable_or_writable_by_another_user(tmp_path: Path) -> None:
    # The private root closes the repository off; inside it, only world-writability is this
    # code's to assert, because group bits follow the operator's umask.
    seen: list[tuple[str, int]] = []

    class _PermissionRecordingScratchGit(_RecordingScratchGit):
        @contextmanager
        def new_scratch_repo(self) -> Iterator[OpenCodeScratchRepo]:
            with super().new_scratch_repo() as repo:
                yield repo
                seen.extend(
                    (str(path), path.stat().st_mode & 0o777) for path in (repo.workdir, *repo.workdir.rglob("*"))
                )

    probe, _ = _probe(tmp_path)
    probe._scratch_git = _PermissionRecordingScratchGit()

    CompatibilityDiagnostic(probe).run()

    assert seen
    root_mode, inside = seen[0][1], seen[1:]
    assert root_mode & 0o077 == 0
    assert [name for name, mode in inside if mode & 0o002] == []


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

    probe, _ = _probe(tmp_path, process=_FailingProcess())

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    summaries = {result.summary for result in report.results}
    assert summaries == {BOUNDARY_FAULT_SUMMARY}
    assert SHAPE_FAULT_SUMMARY not in summaries


def test_a_scratch_repository_fault_is_not_reported_as_an_OpenCode_shape(tmp_path: Path) -> None:
    class _FailingScratchGit(_RecordingScratchGit):
        @contextmanager
        def new_scratch_repo(self) -> Iterator[OpenCodeScratchRepo]:
            raise OSError("the scratch repository could not be created")
            yield  # pragma: no cover - unreachable, present so this stays a generator

    probe, _ = _probe(tmp_path)
    probe._scratch_git = _FailingScratchGit()

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    assert {result.summary for result in report.results} == {INTERNAL_FAULT_SUMMARY}


def test_a_malformed_OpenCode_shape_is_still_attributed_to_OpenCode() -> None:
    probe = OpenCodeCompatibilityProbe.__new__(OpenCodeCompatibilityProbe)

    assert probe._unexpected_error(OpenCodeShapeError("bad field")) == SHAPE_FAULT_SUMMARY
    assert probe._unexpected_error(json.JSONDecodeError("bad", "{", 0)) == SHAPE_FAULT_SUMMARY
    assert probe._unexpected_error(RuntimeError("a runner bug")) == INTERNAL_FAULT_SUMMARY


def test_ignored_runner_config_is_blocking(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    binary = fake_binary(tmp_path / "bin", ignore_config=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "configuration_isolation: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    configuration = next(probe for probe in report["probes"] if probe["name"] == "configuration_isolation")
    assert configuration["state"] == "failed"


@pytest.mark.parametrize("mode", ["prose", "os-error"])
def test_configuration_probe_requires_a_terminal_configured_denial(tmp_path: Path, mode: str) -> None:
    evidence = tmp_path / "evidence"
    binary = fake_binary(
        tmp_path / "bin",
        configuration_prose_only=mode == "prose",
        configuration_os_error=mode == "os-error",
    )

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "configuration_isolation: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    configuration = next(probe for probe in report["probes"] if probe["name"] == "configuration_isolation")
    assert configuration["state"] == "failed"


@pytest.mark.parametrize("version", ["1.18.25-beta.1", "1.18.25+build.1", "1.18.25 extra"])
def test_version_suffix_or_additional_output_blocks_before_scratch_creation(tmp_path: Path, version: str) -> None:
    probe, scratch = _probe(tmp_path, version=version)

    report = CompatibilityDiagnostic(probe).run()

    assert report.classification.value == "blocking"
    assert scratch.path is None
    assert [operation["operation"] for operation in probe.evidence["operations"]] == ["version"]  # type: ignore[index]


def test_malformed_model_is_rejected_before_any_diagnostic_side_effect(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path / "bin")
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
    probe, _ = _probe(tmp_path)

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


def test_mutating_fake_cannot_change_host_auth_file_during_version_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_data = tmp_path / "host-data"
    auth_source = host_data / "opencode" / "auth.json"
    auth_source.parent.mkdir(parents=True)
    auth_source.write_bytes(b"synthetic immutable auth fixture")
    monkeypatch.setenv("XDG_DATA_HOME", str(host_data))
    before = auth_source.read_bytes()

    probe, _ = _probe(tmp_path, mutate_auth=True)
    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is True
    assert auth_source.read_bytes() == before
    assert probe.evidence["xdg"]["auth_provisioned"] is True  # type: ignore[index]


def test_mismatched_binary_cannot_read_disposable_auth_during_version_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_data = tmp_path / "host-data"
    auth_source = host_data / "opencode" / "auth.json"
    auth_source.parent.mkdir(parents=True)
    auth_source.write_bytes(b"synthetic auth fixture")
    monkeypatch.setenv("XDG_DATA_HOME", str(host_data))
    marker = tmp_path / "auth-read-result"
    probe, scratch = _probe(
        tmp_path,
        version="1.18.24",
        read_auth=True,
        auth_read_marker=marker,
    )

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    assert report.observed_version == "1.18.24"
    assert scratch.path is None
    assert not marker.exists()
    assert not probe.evidence["xdg"]["auth_provisioned"]  # type: ignore[index]


def test_static_replayed_final_export_cannot_satisfy_transcript_proof(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    binary = fake_binary(tmp_path / "bin", static_replay=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "fresh_turn: blocking (failed)" in result.output
    assert "transcript_read: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    usage = next(probe for probe in report["probes"] if probe["name"] == "usage_cost")
    assert usage["state"] == "observed"


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


def test_cli_requires_explicit_live_provider_opt_in_and_prints_one_result_per_probe(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path / "bin")
    evidence = tmp_path / "evidence"
    args = _cli_args(binary, evidence)

    missing = CliRunner().invoke(runner_group, args)
    assert missing.exit_code != 0
    assert "live-provider" in missing.output

    result = CliRunner().invoke(runner_group, [*args, "--live-provider"])

    assert result.exit_code == 0, result.output
    assert "OpenCode version: 1.18.25" in result.output
    assert result.output.count("compatibility: degraded") == 1
    for probe in PROBE_ROSTER:
        assert result.output.count(f"{probe.value}: ") == 1
    assert PROVIDER_SECRET not in result.output


def test_version_mismatch_is_reported_as_blocking_and_exits_nonzero(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    binary = fake_binary(tmp_path / "bin", version="1.18.24")

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "OpenCode version: 1.18.24" in result.output
    assert result.output.count("compatibility: blocking") == 1
    for probe in PROBE_ROSTER:
        assert result.output.count(f"{probe.value}: ") == 1
    report = json.loads((evidence / "report.json").read_text())
    assert report["classification"] == "blocking"
    assert report["complete"] is True
    assert report["admissible"] is False


def test_permission_request_without_denial_exits_nonzero_and_retains_complete_report(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    binary = fake_binary(tmp_path / "bin", permission_request_only=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "permission: blocking (failed)" in result.output
    assert result.output.count("compatibility: blocking") == 1
    for probe in PROBE_ROSTER:
        assert result.output.count(f"{probe.value}: ") == 1
    report = json.loads((evidence / "report.json").read_text())
    assert report["classification"] == "blocking"
    assert report["complete"] is True
    assert len(report["probes"]) == len(PROBE_ROSTER)


@pytest.mark.parametrize("mode", ["prose", "duplicate", "os-error", "nonzero"])
def test_permission_probe_rejects_prose_and_more_than_one_denied_call(tmp_path: Path, mode: str) -> None:
    evidence = tmp_path / "evidence"
    binary = fake_binary(
        tmp_path / "bin",
        permission_prose_only=mode == "prose",
        permission_duplicate=mode == "duplicate",
        permission_os_error=mode == "os-error",
        permission_nonzero=mode == "nonzero",
    )

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "permission: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    permission = next(probe for probe in report["probes"] if probe["name"] == "permission")
    assert permission["state"] == "failed"


class _ContractProbe:
    observed_version = PINNED_OPENCODE_VERSION
    expected_version = PINNED_OPENCODE_VERSION

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
