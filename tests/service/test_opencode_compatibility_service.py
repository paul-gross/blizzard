"""Service-tier proof for the OpenCode compatibility diagnostic against a real ``mock-opencode emit``
artifact, spawned and exercised as an out-of-process binary (``bzh:external-cli-fake-is-service-tier``).

Every emitted artifact is written under its own test's ``tmp_path`` — unique per test, safe under
``-n auto``.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
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
)
from blizzard.runner.harness.internal.opencode_attach import LoopbackAttachProxyFactory
from blizzard.runner.harness.internal.opencode_compaction import (
    IOpenCodeCompactor,
    OpenCodeCompactionResult,
    SubprocessOpenCodeCompactor,
    compaction_transition_observed,
)
from blizzard.runner.harness.internal.opencode_diagnostic import run_opencode_compatibility
from blizzard.runner.harness.internal.opencode_evidence import OpenCodeEvidence
from blizzard.runner.harness.internal.opencode_landlock import landlock_version
from blizzard.runner.harness.internal.opencode_loopback import UrllibLoopbackTransport
from blizzard.runner.harness.internal.opencode_probe import (
    INTERNAL_FAULT_SUMMARY,
    PINNED_OPENCODE_VERSION,
    OpenCodeCompatibilityProbe,
)
from blizzard.runner.harness.internal.opencode_process import (
    IOpenCodeProcess,
    OpenCodeStartedProcess,
    SubprocessOpenCodeProcess,
)
from blizzard.runner.harness.internal.opencode_scratch_git import (
    OpenCodeScratchRepo,
    SubprocessOpenCodeScratchGit,
)
from blizzard.runner.harness.internal.opencode_transcript import TranscriptExportSample
from tests.service.support import require_mock_fleet, require_opencode_cli_surface, service_gate

pytestmark = [pytest.mark.service, service_gate]

MODEL = "openai/gpt-5.6-luna"
VARIANT = "max"
PROVIDER_SECRET = "provider-secret-sentinel"

requires_landlock = pytest.mark.skipif(
    landlock_version() < 3,
    reason="the OpenCode process binding confines children with Landlock ABI 3",
)


def _mock_opencode() -> Path:
    return require_opencode_cli_surface(require_mock_fleet())


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


def _fake_binary(
    mock_opencode: Path,
    tmp_path: Path,
    *,
    version: str = PINNED_OPENCODE_VERSION,
    permission_request_only: bool = False,
    permission_prose_only: bool = False,
    permission_duplicate: bool = False,
    permission_os_error: bool = False,
    permission_nonzero: bool = False,
    ignore_config: bool = False,
    drop_config_shell: bool = False,
    drop_config_compaction: bool = False,
    provider_refusal: bool = False,
    configuration_prose_only: bool = False,
    configuration_os_error: bool = False,
    static_replay: bool = False,
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
) -> str:
    """Emit a real ``mock-opencode`` artifact, baked with the named levers — the service-tier
    replacement for the old temp-file ``fake_binary``. Every keyword here maps 1:1 onto a
    ``blizzard_mock.harness.opencode_surface.levers.Lever`` member name; kept in step by hand
    (`bzh:opencode-lever-roster-extends-both-sides`), since this repo cannot import ``blizzard_mock``
    to check mechanically."""
    lever_flags = {
        "PERMISSION_REQUEST_ONLY": permission_request_only,
        "PERMISSION_PROSE_ONLY": permission_prose_only,
        "PERMISSION_DUPLICATE": permission_duplicate,
        "PERMISSION_OS_ERROR": permission_os_error,
        "PERMISSION_NONZERO": permission_nonzero,
        "IGNORE_CONFIG": ignore_config,
        "DROP_CONFIG_SHELL": drop_config_shell,
        "DROP_CONFIG_COMPACTION": drop_config_compaction,
        "PROVIDER_REFUSAL": provider_refusal,
        "CONFIGURATION_PROSE_ONLY": configuration_prose_only,
        "CONFIGURATION_OS_ERROR": configuration_os_error,
        "STATIC_REPLAY": static_replay,
        "FRESH_NONZERO": fresh_nonzero,
        "PROCESS_CONTROL_NO_LIVE_STATE": process_control_no_live_state,
        "TAKEOVER_WRONG_DIRECTORY": takeover_wrong_directory,
        "TAKEOVER_WRONG_SESSION": takeover_wrong_session,
        "TAKEOVER_NON_SSE": takeover_non_sse,
        "TAKEOVER_EXIT_EARLY": takeover_exit_early,
        "TAKEOVER_IDLE_SSE": takeover_idle_sse,
        "TAKEOVER_IMMEDIATE_EOF": takeover_immediate_eof,
        "TAKEOVER_STREAM_FAILURE": takeover_stream_failure,
        "TAKEOVER_EVENT_GATED": takeover_event_gated,
        "SECURITY_COMMAND_EXECUTES": security_command_executes,
        "MUTATE_AUTH": mutate_auth,
        "READ_AUTH": read_auth,
        "COMPACTION_NO_CHANGE": compaction_no_change,
    }
    assert len(lever_flags) == 26, "roster size drifted from blizzard-mock's Lever enum — update both sides"
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "fake-opencode"
    argv = [str(mock_opencode), "emit", "--out", str(out), "--version", version]
    for lever_name, enabled in lever_flags.items():
        if enabled:
            argv += ["--lever", lever_name]
    if auth_read_marker is not None:
        argv += ["--auth-read-marker", str(auth_read_marker)]
    if version_touch_path is not None:
        argv += ["--version-touch-path", version_touch_path.name]
    proc = subprocess.run(argv, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return str(out)


def _probe(
    mock_opencode: Path,
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
            binary=_fake_binary(
                mock_opencode,
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
    mock_opencode = _mock_opencode()
    compactor = SubprocessOpenCodeCompactor(
        SubprocessOpenCodeProcess(),
        lambda child: _stop_compaction_server(child, report_reaped=report_reaped),
        UrllibLoopbackTransport(),
        timeout_seconds=5.0,
    )
    probe, _ = _probe(mock_opencode, tmp_path, compactor=compactor, timeout_seconds=5.0)

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
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, compactor=_FakeCompactor(effective=False))

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    fresh = next(result for result in report.results if result.probe is CompatibilityProbe.FRESH_TURN)
    assert fresh.state is EvidenceState.FAILED
    assert "attributable compaction transition" in fresh.summary
    transcript = next(result for result in report.results if result.probe is CompatibilityProbe.TRANSCRIPT_READ)
    assert transcript.state is EvidenceState.FAILED


@requires_landlock
def test_real_compactor_requires_a_new_transition_after_successful_summarize(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    process = SubprocessOpenCodeProcess()
    compactor = SubprocessOpenCodeCompactor(
        process,
        lambda child: _stop_compaction_server(child, report_reaped=True),
        UrllibLoopbackTransport(),
        timeout_seconds=5.0,
    )
    probe, _ = _probe(mock_opencode, tmp_path, compactor=compactor, compaction_no_change=True, timeout_seconds=5.0)

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
    mock_opencode = _mock_opencode()
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

    probe, scratch = _probe(mock_opencode, tmp_path)
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
    mock_opencode = _mock_opencode()
    caller_marker = tmp_path / "caller-version-touch"
    probe, scratch = _probe(mock_opencode, tmp_path, version_touch_path=caller_marker)

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
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    probe, _ = _probe(mock_opencode, tmp_path, fresh_nonzero=True)

    report = run_opencode_compatibility(probe, OpenCodeEvidence(evidence, secrets=probe.secret_values))

    assert report.admissible is False
    fresh = next(result for result in report.results if result.probe is CompatibilityProbe.FRESH_TURN)
    assert fresh.state is EvidenceState.FAILED
    assert "exited with status 7" in fresh.summary
    usage = next(result for result in report.results if result.probe is CompatibilityProbe.USAGE_COST)
    assert usage.state is EvidenceState.FAILED


def test_process_control_requires_the_requested_tool_to_be_live(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, process_control_no_live_state=True, timeout_seconds=1.5)

    report = CompatibilityDiagnostic(probe).run()

    process_control = next(result for result in report.results if result.probe is CompatibilityProbe.PROCESS_CONTROL)
    assert process_control.state is EvidenceState.FAILED
    assert "live state" in process_control.summary
    assert probe.evidence["process_control"]["live_state_observed"] is False  # type: ignore[index]


def test_takeover_requires_recorded_session_workdir_signal(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, takeover_wrong_directory=True)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.FAILED
    assert "scratch workdir" in takeover.summary


def test_takeover_requires_the_exported_session_identity(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, takeover_wrong_session=True)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.FAILED
    assert "identity" in takeover.summary


def test_takeover_rejects_a_non_sse_upstream_response(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, takeover_non_sse=True, timeout_seconds=1.5)

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
    mock_opencode = _mock_opencode()
    probe, _ = _probe(
        mock_opencode,
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
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, takeover_idle_sse=True, timeout_seconds=1.5)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.OBSERVED
    assert probe.evidence["takeover"]["event_stream_valid"] is True  # type: ignore[index]
    assert probe.evidence["takeover"]["event_stream_bytes"] == 0  # type: ignore[index]


def test_takeover_triggers_local_activity_to_complete_a_gated_upstream_handshake(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, takeover_event_gated=True, timeout_seconds=2.0)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.OBSERVED
    trigger = next(item for item in probe.evidence["operations"] if item["operation"] == "takeover_event_trigger")  # type: ignore[index]
    assert trigger["http"] == {"method": "POST", "path": "/session", "status": 200}  # type: ignore[index]


def test_takeover_requires_client_liveness_after_the_real_sse_handshake(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, takeover_exit_early=True, timeout_seconds=1.5)

    report = CompatibilityDiagnostic(probe).run()

    takeover = next(result for result in report.results if result.probe is CompatibilityProbe.TAKEOVER)
    assert takeover.state is EvidenceState.FAILED
    assert "remain alive" in takeover.summary


def test_permission_probe_requires_an_explicit_denied_tool_call_not_model_text(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path)

    report = CompatibilityDiagnostic(probe).run()

    permission = next(result for result in report.results if result.probe is CompatibilityProbe.PERMISSION)
    assert permission.state is EvidenceState.OBSERVED
    assert permission.classification.value == "supported"


def test_permission_probe_blocks_when_a_security_command_executes(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, security_command_executes=True)

    report = CompatibilityDiagnostic(probe).run()

    permission = next(result for result in report.results if result.probe is CompatibilityProbe.PERMISSION)
    assert permission.state is EvidenceState.FAILED
    assert "explicitly denied" in permission.summary


@requires_landlock
def test_effective_config_without_the_runner_shell_is_blocking(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    binary = _fake_binary(mock_opencode, tmp_path / "bin", drop_config_shell=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "configuration_isolation: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    configuration = next(probe for probe in report["probes"] if probe["name"] == "configuration_isolation")
    assert configuration["summary"] == "OpenCode did not resolve the runner-owned model-tool shell"


@requires_landlock
def test_effective_config_without_the_runner_compaction_bound_is_blocking(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    binary = _fake_binary(mock_opencode, tmp_path / "bin", drop_config_compaction=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "configuration_isolation: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    configuration = next(probe for probe in report["probes"] if probe["name"] == "configuration_isolation")
    assert configuration["summary"] == "OpenCode did not resolve the runner-owned compaction tail bound"


@requires_landlock
def test_a_provider_refusal_is_not_reported_as_an_OpenCode_timeout(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, _ = _probe(mock_opencode, tmp_path, provider_refusal=True, timeout_seconds=3.0)

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    # The provider answered before OpenCode could act, so no probe observed a contract at all.
    assert {result.state for result in report.results} == {EvidenceState.AMBIGUOUS}
    assert {result.summary for result in report.results} == {"the provider refused the request with status 429"}


@requires_landlock
def test_disposable_scratch_repository_is_never_reachable_or_writable_by_another_user(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
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

    probe, _ = _probe(mock_opencode, tmp_path)
    probe._scratch_git = _PermissionRecordingScratchGit()

    CompatibilityDiagnostic(probe).run()

    assert seen
    root_mode, inside = seen[0][1], seen[1:]
    assert root_mode & 0o077 == 0
    assert [name for name, mode in inside if mode & 0o002] == []


def test_a_scratch_repository_fault_is_not_reported_as_an_OpenCode_shape(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()

    class _FailingScratchGit(_RecordingScratchGit):
        @contextmanager
        def new_scratch_repo(self) -> Iterator[OpenCodeScratchRepo]:
            raise OSError("the scratch repository could not be created")
            yield  # pragma: no cover - unreachable, present so this stays a generator

    probe, _ = _probe(mock_opencode, tmp_path)
    probe._scratch_git = _FailingScratchGit()

    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is False
    assert {result.summary for result in report.results} == {INTERNAL_FAULT_SUMMARY}


def test_ignored_runner_config_is_blocking(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    binary = _fake_binary(mock_opencode, tmp_path / "bin", ignore_config=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "configuration_isolation: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    configuration = next(probe for probe in report["probes"] if probe["name"] == "configuration_isolation")
    assert configuration["state"] == "failed"


@pytest.mark.parametrize("mode", ["prose", "os-error"])
def test_configuration_probe_requires_a_terminal_configured_denial(tmp_path: Path, mode: str) -> None:
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    binary = _fake_binary(
        mock_opencode,
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


@pytest.mark.parametrize("version", ["1.18.25-beta.1", "1.18.25 extra"])
def test_version_suffix_or_additional_output_blocks_before_scratch_creation(tmp_path: Path, version: str) -> None:
    """A pre-release suffix stays excluded from the admitted range regardless (D2's
    ``prereleases=False``), and multi-token ``--version`` output never even normalizes to a
    parseable version — both block before the scratch repository is ever created."""
    mock_opencode = _mock_opencode()
    probe, scratch = _probe(mock_opencode, tmp_path, version=version)

    report = CompatibilityDiagnostic(probe).run()

    assert report.classification.value == "blocking"
    assert scratch.path is None
    assert [operation["operation"] for operation in probe.evidence["operations"]] == ["version"]  # type: ignore[index]


def test_a_local_version_suffix_is_admitted_by_range_membership(tmp_path: Path) -> None:
    """Unlike the old exact-equality pin, range membership never special-cases a local
    version identifier: `1.18.25+build.1` orders at or above `1.18.25`, so it is admitted and
    the run proceeds past the version gate."""
    mock_opencode = _mock_opencode()
    probe, scratch = _probe(mock_opencode, tmp_path, version="1.18.25+build.1")

    report = CompatibilityDiagnostic(probe).run()

    assert report.version_admitted is True
    assert scratch.path is not None
    assert report.classification.value != "blocking"


def test_a_version_above_the_pinned_corpus_but_inside_the_range_is_admitted(tmp_path: Path) -> None:
    """`1.18.31` is above the only committed corpus but inside `ADMITTED_OPENCODE_RANGE`; the
    live gate admits it on range membership alone — reference-corpus resolution is
    `classify_offline`'s own concern (`tests/test_runner_harness_offline_compatibility.py`)."""
    mock_opencode = _mock_opencode()
    probe, scratch = _probe(mock_opencode, tmp_path, version="1.18.31")

    report = CompatibilityDiagnostic(probe).run()

    assert report.version_admitted is True
    assert report.observed_version == "1.18.31"
    assert scratch.path is not None
    assert report.classification.value != "blocking"


def test_a_version_at_or_above_the_upper_bound_is_rejected(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    probe, scratch = _probe(mock_opencode, tmp_path, version="2.0.0")

    report = CompatibilityDiagnostic(probe).run()

    assert report.version_admitted is False
    assert report.classification.value == "blocking"
    assert scratch.path is None


def test_mutating_fake_cannot_change_host_auth_file_during_version_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_opencode = _mock_opencode()
    host_data = tmp_path / "host-data"
    auth_source = host_data / "opencode" / "auth.json"
    auth_source.parent.mkdir(parents=True)
    auth_source.write_bytes(b"synthetic immutable auth fixture")
    monkeypatch.setenv("XDG_DATA_HOME", str(host_data))
    before = auth_source.read_bytes()

    probe, _ = _probe(mock_opencode, tmp_path, mutate_auth=True)
    report = CompatibilityDiagnostic(probe).run()

    assert report.admissible is True
    assert auth_source.read_bytes() == before
    assert probe.evidence["xdg"]["auth_provisioned"] is True  # type: ignore[index]


def test_mismatched_binary_cannot_read_disposable_auth_during_version_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_opencode = _mock_opencode()
    host_data = tmp_path / "host-data"
    auth_source = host_data / "opencode" / "auth.json"
    auth_source.parent.mkdir(parents=True)
    auth_source.write_bytes(b"synthetic auth fixture")
    monkeypatch.setenv("XDG_DATA_HOME", str(host_data))
    marker = tmp_path / "auth-read-result"
    probe, scratch = _probe(
        mock_opencode,
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
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    binary = _fake_binary(mock_opencode, tmp_path / "bin", static_replay=True)

    result = CliRunner().invoke(runner_group, [*_cli_args(binary, evidence), "--live-provider"])

    assert result.exit_code == 1
    assert "fresh_turn: blocking (failed)" in result.output
    assert "transcript_read: blocking (failed)" in result.output
    report = json.loads((evidence / "report.json").read_text())
    usage = next(probe for probe in report["probes"] if probe["name"] == "usage_cost")
    assert usage["state"] == "observed"


def test_cli_requires_explicit_live_provider_opt_in_and_prints_one_result_per_probe(tmp_path: Path) -> None:
    mock_opencode = _mock_opencode()
    binary = _fake_binary(mock_opencode, tmp_path / "bin")
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
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    binary = _fake_binary(mock_opencode, tmp_path / "bin", version="1.18.24")

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
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    binary = _fake_binary(mock_opencode, tmp_path / "bin", permission_request_only=True)

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
    mock_opencode = _mock_opencode()
    evidence = tmp_path / "evidence"
    binary = _fake_binary(
        mock_opencode,
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
