"""The Claude Code adapter — verdict parsing (unit) and a real subprocess (component).

``parse_verdict`` is exercised over the harness-native JSON envelope and its failure
modes; the component test drives spawn/resume against a real fake-harness binary. Also
covers usage parsing, the per-lease stdout redirect, and node-entry resume.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import ResumeHandle, WorkerPreamble
from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.node_steps.envelope import Envelope, GraphArtifact
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
from blizzard.runner.process.child_processes import (
    ChildRun,
    IPtyChild,
    ISpawnedChild,
    ShellCommand,
    Stream,
    StreamTarget,
)
from tests.support import transcript_fixtures
from tests.support.child_launch import CHILDREN, tool_env, version_probe
from tests.support.claude_code_adapter import SENTINEL_UNLISTED_VAR as _SENTINEL_UNLISTED_VAR
from tests.support.claude_code_adapter import adapter as _adapter
from tests.support.runner_fakes import FakeProbe, make_envelope
from tests.support.suite_plugin import DAEMON_ENV

_JSON_PASS = '{"type":"result","subtype":"success","is_error":false,"result":"Looks good. <Choice>pass</Choice>","session_id":"s1"}'


def _which_on_usr_bin(binary: str, path: object = None) -> str:
    """Stands in for ``shutil.which``, resolving every binary under ``/usr/bin``."""
    return f"/usr/bin/{binary}"


def _which_finds_nothing(binary: str, path: object = None) -> str | None:
    """Stands in for ``shutil.which`` on a ``PATH`` holding no binary at all."""
    return None


@pytest.fixture(autouse=True)
def _claude_resolves_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A unit test's fake spawn must not depend on whether ``claude`` is really installed
    on this machine's ``PATH`` — `ensure_executable`'s `shutil.which` lookup runs
    before the faked ``subprocess.Popen`` ever sees the call. Tests proving the
    absent-from-``PATH`` behavior override this within their own body."""
    monkeypatch.setattr(shutil, "which", _which_on_usr_bin)


class _ScriptedRuns:
    """A launch seam whose ``run`` answers ``reply`` (or raises it), recording nothing else."""

    def __init__(self, reply: ChildRun | BaseException) -> None:
        self._reply = reply

    def run(
        self,
        command: Sequence[str] | ShellCommand,
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        timeout: float,
        input: str | None = None,
        input_settle: float = 0.0,
        stdout: IO[bytes] | None = None,
    ) -> ChildRun:
        if isinstance(self._reply, BaseException):
            raise self._reply
        return self._reply

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        stdin: StreamTarget = Stream.DEVNULL,
        stdout: StreamTarget = Stream.DEVNULL,
        stderr: StreamTarget = Stream.DEVNULL,
        child_setup: Callable[[], None] | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ISpawnedChild:
        raise NotImplementedError

    def spawn_pty(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str,
        env: ChildEnv,
        child_setup: Callable[[], None] | None = None,
    ) -> IPtyChild:
        raise NotImplementedError

    def run_foreground(self, command: ShellCommand, *, cwd: Path | str, env: ChildEnv) -> int:
        raise NotImplementedError


def _scripted_versions(reply: ChildRun | BaseException) -> VersionProbe:
    return VersionProbe(_ScriptedRuns(reply), tool_env())


def test_parse_verdict_extracts_choice_from_json_envelope(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor).parse_verdict(_JSON_PASS) == "pass"


def test_parse_verdict_reads_plain_text_reply(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor).parse_verdict("verdict: <Choice>fail</Choice>") == "fail"


def test_parse_verdict_missing_choice_is_none(spawn_executor: Executor) -> None:
    assert (
        _adapter(spawn_executor).parse_verdict('{"type":"result","result":"no verdict here","session_id":"s1"}') is None
    )
    # A delivered judgement whose reply has no parseable <Choice> is a failure:
    # an unclosed tag, whitespace-only name, and a bare open tag all read as None.
    assert _adapter(spawn_executor).parse_verdict("<Choice>") is None
    assert _adapter(spawn_executor).parse_verdict("<Choice></Choice>") is None
    assert _adapter(spawn_executor).parse_verdict("<Choice>   </Choice>") is None


def test_parse_assessment_returns_text_after_the_choice(spawn_executor: Executor) -> None:
    output = '{"type":"result","result":"<Choice>fail</Choice>\\nBLOCKING: guard empty input","session_id":"s1"}'
    assert _adapter(spawn_executor).parse_assessment(output) == "BLOCKING: guard empty input"


def test_parse_assessment_is_empty_without_a_choice(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor).parse_assessment("no verdict at all") == ""


def test_resume_command_is_the_literal_takeover(spawn_executor: Executor) -> None:
    cmd = _adapter(spawn_executor, binary="claude").resume_command("/ws/e1", "sess-x")
    assert cmd == "cd /ws/e1 && claude --resume sess-x"


def test_attended_resume_command_reasserts_the_permission_mode(spawn_executor: Executor) -> None:
    # The flag is per-invocation, not session-sticky: the takeover door's
    # exec'd command reasserts it so a bypassPermissions worker is not demoted mid-task.
    adapter = _adapter(spawn_executor, binary="claude", permission_mode="bypassPermissions")

    cmd = adapter.resume_command("/ws/e1", "sess-x", model="opus", attended=True)

    assert cmd == "cd /ws/e1 && claude --resume sess-x --model opus --permission-mode bypassPermissions"


def test_the_advertised_paste_string_never_carries_the_permission_mode(spawn_executor: Executor) -> None:
    # The default composition is the escalation record / `runner status` paste string:
    # a human runs it in a bare terminal, so it stays at the interactive default.
    adapter = _adapter(spawn_executor, binary="claude", permission_mode="bypassPermissions")

    assert adapter.resume_command("/ws/e1", "sess-x") == "cd /ws/e1 && claude --resume sess-x"


def test_resume_command_with_an_empty_permission_override_stays_bare(spawn_executor: Executor) -> None:
    assert (
        _adapter(spawn_executor, binary="claude", permission_mode="").resume_command("/ws/e1", "sess-x", attended=True)
        == "cd /ws/e1 && claude --resume sess-x"
    )


# --------------------------------------------------------------------------- #
# `observe_version` (bounded and non-raising — read AFTER a worker is already live).


def test_observe_version_reads_the_binarys_version_output(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    monkeypatch.setattr(shutil, "which", _which_on_usr_bin)
    versions = _scripted_versions(ChildRun(exit_code=0, stdout="claude-code 1.2.3\n", stderr="", timed_out=False))
    assert _adapter(spawn_executor, binary="claude", versions=versions).observe_version() == "claude-code 1.2.3"


def test_observe_version_times_out_to_none_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    # On PATH per `which` — the timeout is the launch's own, not a missing-binary skip.
    monkeypatch.setattr(shutil, "which", _which_on_usr_bin)
    versions = _scripted_versions(ChildRun(exit_code=None, stdout="", stderr="", timed_out=True))

    with capture_logs() as logs:
        assert _adapter(spawn_executor, binary="claude", versions=versions).observe_version() is None
    assert any(entry["event"] == "harness version probe failed" for entry in logs)


def test_observe_version_missing_binary_reads_none_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    """A binary that resolves on ``PATH`` but fails at exec time (a race, a permission
    problem) still reads ``None`` and logs — distinct from never being on ``PATH`` at all
    (below), which skips the subprocess attempt entirely."""

    monkeypatch.setattr(shutil, "which", _which_on_usr_bin)
    versions = _scripted_versions(OSError("no such file or directory: 'claude'"))

    with capture_logs() as logs:
        assert _adapter(spawn_executor, binary="claude", versions=versions).observe_version() is None
    assert any(entry["event"] == "harness version probe failed" for entry in logs)


def test_observe_version_absent_from_path_skips_the_subprocess_and_does_not_warn(
    monkeypatch: pytest.MonkeyPatch,
    spawn_executor: Executor,
) -> None:
    """A host that never installed this binding's binary (a runner configured with only
    one of several known harnesses) is an expected shape, not a failure — no subprocess
    attempt, and nothing louder than ``debug``."""
    monkeypatch.setattr(shutil, "which", _which_finds_nothing)

    versions = _scripted_versions(AssertionError("no launch may be attempted for a binary absent from PATH"))

    with capture_logs() as logs:
        assert _adapter(spawn_executor, binary="claude", versions=versions).observe_version() is None
    assert not any(entry["log_level"] == "warning" for entry in logs)


# --------------------------------------------------------------------------- #
# Node-entry resume: the CLI flag branch, with ``subprocess.Popen`` faked.


class _FakeSpawnedProcess:
    """A stand-in for the ``Popen`` handle ``spawn`` reads ``.pid`` off of.

    An implausibly large pid so ``ProcStat.of`` finds nothing and returns
    ``None`` gracefully, as it does for any already-exited/foreign pid."""

    pid = 9_999_999


def _fake_popen_capturing(captured: dict[str, list[str]]) -> object:
    def _fake_popen(cmd: list[str], **kwargs: object) -> _FakeSpawnedProcess:
        captured["cmd"] = cmd
        return _FakeSpawnedProcess()

    return _fake_popen


def _spawn_fixture(spawn_executor: Executor) -> tuple[ClaudeCodeAdapter, Envelope, WorkerPreamble]:
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )
    return adapter, envelope, preamble


def test_spawn_with_resume_from_emits_resume_flag_and_echoes_its_continuation_id(
    monkeypatch: pytest.MonkeyPatch,
    spawn_executor: Executor,
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    handle = adapter.spawn(envelope, preamble, session_hint="fresh-hint", resume_from="prior-sid").await_identity(0)

    cmd = captured["cmd"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "prior-sid"
    assert "--session-id" not in cmd
    # In-place (Q1): the continuation stays under the resumed id, not the hint.
    assert handle.session_id == "prior-sid"


def test_spawn_without_resume_from_is_unchanged(monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    handle = adapter.spawn(envelope, preamble, session_hint="fresh-hint").await_identity(0)

    cmd = captured["cmd"]
    assert "--session-id" in cmd
    assert cmd[cmd.index("--session-id") + 1] == "fresh-hint"
    assert "--resume" not in cmd
    assert handle.session_id == "fresh-hint"


def test_spawn_omits_settings_without_a_bundle_or_settings_path(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    adapter.spawn(envelope, preamble, session_hint="sid").await_identity(0)

    assert "--settings" not in captured["cmd"]


def test_spawn_stamps_process_start_time_from_the_injected_probe(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    # `_FakeSpawnedProcess.pid` is implausibly large so the real `/proc` reader would find
    # nothing — proving the stamp came from the injected probe, not a fallback to `/proc`.
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing({}))
    probe = FakeProbe(alive={(_FakeSpawnedProcess.pid, "fake-start-time-token")})
    adapter = ClaudeCodeAdapter(
        binary="claude",
        worker_env=AllowlistedEnv.of(()),
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor, children=CHILDREN),
        versions=version_probe(),
    )
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="fresh-hint")

    assert handle.pid == _FakeSpawnedProcess.pid
    assert handle.process_start_time == "fake-start-time-token"


def test_judge_stamps_process_start_time_from_the_injected_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing({}))
    probe = FakeProbe(alive={(_FakeSpawnedProcess.pid, "fake-judge-start-time")})
    adapter = ClaudeCodeAdapter(
        binary="claude",
        worker_env=AllowlistedEnv.of(()),
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor, children=CHILDREN),
        versions=version_probe(),
    )
    workdir = tmp_path / "e1"
    workdir.mkdir()

    handle = adapter.judge(str(workdir), "sess-123", "Assess the build.", str(workdir / "judge-output.json"))

    assert handle.pid == _FakeSpawnedProcess.pid
    assert handle.process_start_time == "fake-judge-start-time"


def test_resume_with_message_stamps_process_start_time_and_a_real_confirm_durable(
    monkeypatch: pytest.MonkeyPatch,
    spawn_executor: Executor,
) -> None:
    """A resume gets the same ownership a fresh spawn or judge gets — the
    launcher's own recorded start time, and a real disarm signal, not
    `ResumeHandle`'s bare no-op default."""
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing({}))
    probe = FakeProbe(alive={(_FakeSpawnedProcess.pid, "fake-resume-start-time")})
    adapter = ClaudeCodeAdapter(
        binary="claude",
        worker_env=AllowlistedEnv.of(()),
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor, children=CHILDREN),
        versions=version_probe(),
    )

    resumed = adapter.resume_with_message("/ws", "sess-123", "continue")

    assert resumed.pid == _FakeSpawnedProcess.pid
    assert resumed.process_start_time == "fake-resume-start-time"
    assert resumed.confirm_durable is not ResumeHandle.__dataclass_fields__["confirm_durable"].default


# --------------------------------------------------------------------------- #
# Spawn-environment allowlist: no call path copies `os.environ` wholesale


def test_spawn_env_excludes_the_hub_token_and_an_unlisted_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    spawn_executor: Executor,
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN", "super-secret-token")
    monkeypatch.setenv(_SENTINEL_UNLISTED_VAR, "should-not-leak")
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter.spawn_env(envelope, preamble, "sess-1")

    assert "BZ_HUB_TOKEN" not in env
    assert _SENTINEL_UNLISTED_VAR not in env


def test_spawn_env_excludes_the_elicitation_marker(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter.spawn_env(envelope, preamble, "sess-1")

    assert "BLIZZARD_ELICITATION" not in env


def test_spawn_env_forwards_a_named_passthrough_var(monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor) -> None:
    monkeypatch.setenv("MY_HARNESS_QUIRK", "needed-by-the-real-binary")
    adapter = _adapter(
        spawn_executor=spawn_executor, binary="claude", worker_env=AllowlistedEnv.of(("MY_HARNESS_QUIRK",))
    )
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter.spawn_env(envelope, preamble, "sess-1")

    assert env["MY_HARNESS_QUIRK"] == "needed-by-the-real-binary"


def test_spawn_env_forwards_lc_prefixed_locale_vars(monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor) -> None:
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LC_TIME", "fr_FR.UTF-8")
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter.spawn_env(envelope, preamble, "sess-1")

    assert env["LC_ALL"] == "en_US.UTF-8"
    assert env["LC_TIME"] == "fr_FR.UTF-8"


def test_spawn_env_still_carries_the_base_allowlist_and_deliberate_blizzard_vars(
    monkeypatch: pytest.MonkeyPatch,
    spawn_executor: Executor,
) -> None:
    # The allowlist is not a denylist rewrite in disguise: PATH/HOME and the adapter's
    # own BLIZZARD_* additions still ride the child env.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/worker")
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter.spawn_env(envelope, preamble, "sess-1")

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/worker"
    assert env["BLIZZARD_LEASE_ID"] == "lease_1"
    assert env["BLIZZARD_SESSION_ID"] == "sess-1"


def test_spawn_env_carries_the_lease_capability_token(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    # — the preamble's plaintext lease token rides the spawn env
    # as an explicit per-spawn identity var, alongside BLIZZARD_LEASE_ID.
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        lease_token="plaintext-lease-token",
    )

    env = adapter.spawn_env(envelope, preamble, "sess-1")

    assert env["BLIZZARD_LEASE_TOKEN"] == "plaintext-lease-token"


def test_spawn_env_carries_the_scratch_directory_when_the_preamble_names_one(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        tmpdir="/runner/worker-tmp/lease_1",
    )

    env = adapter.spawn_env(envelope, preamble, "sess-1")

    assert env["BLIZZARD_TMPDIR"] == "/runner/worker-tmp/lease_1"


def test_spawn_env_excludes_the_scratch_directory_when_the_preamble_names_none(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter.spawn_env(envelope, preamble, "sess-1")

    assert "BLIZZARD_TMPDIR" not in env


def test_the_suites_worker_identity_strip_list_covers_every_var_the_adapter_can_inject(
    spawn_executor: Executor,
) -> None:
    """The conftest strip-list agrees with every ``BLIZZARD_*`` var any adapter injection path
    can add, judge's elicitation marker included — add one to a path without adding it here
    and fail, rather than only in a fleet worker where nobody is watching a red suite."""
    adapter = _adapter(spawn_executor, binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        lease_token="plaintext-lease-token",
        tmpdir="/runner/worker-tmp/lease_1",
    )

    spawn_injected = {k for k in adapter.spawn_env(envelope, preamble, "sess-1") if k.startswith("BLIZZARD_")}
    judge_injected = {
        k for k in adapter.identity_env(preamble, "ch_1", "sess-1", elicitation=True) if k.startswith("BLIZZARD_")
    }

    assert (spawn_injected | judge_injected) - set(DAEMON_ENV["unset"]) == set()


def test_worker_judge_and_resumes_keep_a_model_different_from_the_ambient_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    _, envelope, preamble = _spawn_fixture(spawn_executor)
    adapter = _adapter(spawn_executor, binary="claude", model="fable")
    expected = {"--model": "opus", "--effort": "high", "--autocompact": "150k"}

    adapter.spawn(envelope, preamble, session_hint="sid", model="opus", effort="high", compaction_window="150k")
    for flag, value in expected.items():
        assert captured["cmd"][captured["cmd"].index(flag) + 1] == value

    adapter.judge(
        "/ws", "sid", "assess", str(tmp_path / "judge.json"), model="opus", effort="high", compaction_window="150k"
    )
    for flag, value in expected.items():
        assert captured["cmd"][captured["cmd"].index(flag) + 1] == value

    adapter.resume_with_message("/ws", "sid", "continue", model="opus", effort="high", compaction_window="150k")
    for flag, value in expected.items():
        assert captured["cmd"][captured["cmd"].index(flag) + 1] == value

    adapter.spawn(
        envelope, preamble, session_hint="sid", resume_from="sid", model="opus", effort="high", compaction_window="150k"
    )
    for flag, value in expected.items():
        assert captured["cmd"][captured["cmd"].index(flag) + 1] == value

    adapter.spawn(envelope, preamble, session_hint="other", model="sonnet")
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "sonnet"


_USAGE_ENVELOPE = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "<Choice>pass</Choice>",
        "session_id": "s1",
        "model": "claude-opus-4-8",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 45,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 15,
        },
        "total_cost_usd": 0.042,
    }
)


def test_parse_usage_extracts_tokens_and_cost_from_json_envelope(spawn_executor: Executor) -> None:
    sample = _adapter(spawn_executor).parse_usage(_USAGE_ENVELOPE, "judge")
    assert sample is not None
    assert sample.kind == "judge"
    assert sample.model == "claude-opus-4-8"
    assert sample.input_tokens == 120
    assert sample.output_tokens == 45
    assert sample.cache_read_tokens == 30
    assert sample.cache_create_tokens == 15
    assert sample.cost_usd == 0.042


_SIGINT_ENVELOPE = json.dumps(
    {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "session_id": "s1",
        "model": "claude-opus-4-8",
        "usage": {
            "input_tokens": 80,
            "output_tokens": 12,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 0,
        },
        "total_cost_usd": 0.019,
        "modelUsage": {
            "claude-opus-4-8": {
                "inputTokens": 80,
                "outputTokens": 12,
                "cacheReadInputTokens": 5,
                "cacheCreationInputTokens": 0,
            }
        },
    }
)


def test_parse_usage_extracts_a_real_cost_from_a_sigint_error_during_execution_envelope(
    spawn_executor: Executor,
) -> None:
    """The drain's own SIGINT leaves this exact envelope shape — `parse_usage`
    gates on neither `is_error` nor `subtype`, so the real `total_cost_usd` still lands."""
    sample = _adapter(spawn_executor).parse_usage(_SIGINT_ENVELOPE, "spawn")
    assert sample is not None
    assert sample.cost_usd == 0.019
    assert sample.input_tokens == 80
    assert sample.output_tokens == 12


def test_has_usable_output_is_true_for_a_sigint_error_during_execution_envelope(spawn_executor: Executor) -> None:
    """A judge that ends in this envelope was judged, not lost: it names no verdict, so
    the attempt fails and consumes a retry rather than relaunching indefinitely."""
    assert _adapter(spawn_executor).has_usable_output(_SIGINT_ENVELOPE) is True


def test_parse_usage_returns_none_without_a_result_envelope(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor).parse_usage("not json at all", "spawn") is None
    assert _adapter(spawn_executor).parse_usage("", "spawn") is None


def test_parse_usage_returns_none_when_envelope_has_no_usage_object(spawn_executor: Executor) -> None:
    # A killed/verdict-less worker's envelope (if any) carries no `usage` at all —
    # the caller's cue to fall back to `sum_transcript_usage`.
    envelope = json.dumps({"type": "result", "result": "<Choice>pass</Choice>", "session_id": "s1"})
    assert _adapter(spawn_executor).parse_usage(envelope, "spawn") is None


def test_parse_usage_marks_model_unknown_when_envelope_and_transcript_omit_it(spawn_executor: Executor) -> None:
    envelope = json.dumps(
        {
            "type": "result",
            "result": "ok",
            "session_id": "s1",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )
    sample = _adapter(spawn_executor, model="claude-sonnet-5").parse_usage(envelope, "resume")
    assert sample is not None
    assert sample.model == "unknown"
    assert sample.cost_usd is None  # no `total_cost_usd` in this envelope — absent, never fabricated


def test_parse_usage_missing_token_fields_default_to_zero(spawn_executor: Executor) -> None:
    envelope = json.dumps({"type": "result", "result": "ok", "session_id": "s1", "usage": {}})
    sample = _adapter(spawn_executor).parse_usage(envelope, "spawn")
    assert sample is not None
    counts = (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens)
    assert counts == (0, 0, 0, 0)


def test_sum_transcript_usage_sums_multiple_assistant_messages(spawn_executor: Executor) -> None:
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-8",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 1,
                        "cache_creation_input_tokens": 2,
                    },
                    "content": [{"type": "text", "text": "ok"}],
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-8",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 8,
                        "cache_read_input_tokens": 3,
                        "cache_creation_input_tokens": 4,
                    },
                    "content": [{"type": "text", "text": "more"}],
                },
            }
        ),
    ]

    sample = _adapter(spawn_executor).sum_transcript_usage(lines, "resume")

    assert sample.kind == "resume"
    assert sample.model == "claude-opus-4-8"
    assert sample.input_tokens == 30
    assert sample.output_tokens == 13
    assert sample.cache_read_tokens == 4
    assert sample.cache_create_tokens == 6
    assert sample.cost_usd is None  # a transcript carries no dollar figure — the envelope-less fallback


def test_sum_transcript_usage_counts_one_message_once_however_many_records_carry_it(spawn_executor: Executor) -> None:
    """A reply with several content blocks is written as several records that each repeat
    their message's ONE usage. Summing per record inflated the fallback by ~1.7x against the
    billed figure on a real long session (406 of its 629 messages were split in two)."""
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 1_000,
        "cache_creation_input_tokens": 20,
    }
    message = {"role": "assistant", "id": "msg_split", "model": "claude-sonnet-5", "usage": usage}
    lines = [
        json.dumps({"type": "assistant", "message": {**message, "content": [{"type": "thinking"}]}, "uuid": "a1"}),
        json.dumps({"type": "assistant", "message": {**message, "content": [{"type": "text"}]}, "uuid": "a2"}),
        json.dumps({"type": "assistant", "message": {**message, "content": [{"type": "tool_use"}]}, "uuid": "a3"}),
    ]

    sample = _adapter(spawn_executor).sum_transcript_usage(lines, "spawn")

    assert (sample.input_tokens, sample.output_tokens) == (10, 5)
    assert (sample.cache_read_tokens, sample.cache_create_tokens) == (1_000, 20)


def test_sum_transcript_usage_counts_an_id_less_record_rather_than_collapsing_it(spawn_executor: Executor) -> None:
    """An unidentifiable message cannot be collapsed against anything, and is more likely one
    message than a repeat of the last — undercounting spend is the worse failure here."""

    def _record(message_id: str | None, tokens: int) -> str:
        message: dict[str, object] = {
            "role": "assistant",
            "model": "claude-sonnet-5",
            "usage": {
                "input_tokens": tokens,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
        if message_id is not None:
            message["id"] = message_id
        return json.dumps({"type": "assistant", "message": message})

    sample = _adapter(spawn_executor).sum_transcript_usage([_record(None, 7), _record(None, 11)], "spawn")

    assert sample.input_tokens == 18


def test_sum_transcript_usage_ignores_non_assistant_and_malformed_lines(spawn_executor: Executor) -> None:
    lines = [
        "",
        "not json",
        "{truncated json",
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
        json.dumps([1, 2, 3]),  # valid JSON, not a dict record
        json.dumps({"type": "assistant", "message": "not-a-dict"}),
        json.dumps({"type": "assistant", "message": {"usage": "not-a-dict"}}),
        # A non-assistant record carrying usage is still never summed.
        json.dumps({"type": "user", "message": {"id": "u1", "usage": {"input_tokens": 1000}}}),
        transcript_fixtures.assistant_usage(input_tokens=2, output_tokens=3, message_id="m1"),
        transcript_fixtures.assistant_usage(input_tokens=2, output_tokens=3, message_id="m1"),
        # A record naming only some fields counts the rest as zero.
        json.dumps({"type": "assistant", "message": {"id": "m2", "usage": {"cache_read_input_tokens": 7}}}),
        json.dumps({"type": "assistant", "message": {"id": "m3", "usage": {"cache_creation_input_tokens": 11}}}),
    ]

    sample = _adapter(spawn_executor).sum_transcript_usage(lines, "spawn")

    # Every skipped line passes over to the records after it, which alone are summed.
    counts = (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens)
    assert counts == (2, 3, 7, 11)
    assert sample.cost_usd is None


def test_sum_transcript_usage_reads_a_non_numeric_token_field_as_absent(spawn_executor: Executor) -> None:
    usage = {
        "input_tokens": "12",
        "output_tokens": ["x"],
        "cache_read_input_tokens": "many",
        "cache_creation_input_tokens": {"n": 1},
    }
    lines = [
        json.dumps({"type": "assistant", "message": {"id": "m1", "usage": usage}}),
        transcript_fixtures.assistant_usage(input_tokens=2, output_tokens=3, message_id="m2"),
    ]

    sample = _adapter(spawn_executor).sum_transcript_usage(lines, "spawn")

    counts = (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens)
    assert counts == (14, 3, 0, 0)


def test_parse_usage_reads_non_numeric_token_and_model_usage_fields_as_absent(spawn_executor: Executor) -> None:
    envelope = json.dumps(
        {
            "type": "result",
            "result": "ok",
            "model": "claude-opus-4-8",
            "usage": {"input_tokens": "lots", "output_tokens": 45.7, "cache_read_input_tokens": None},
            "modelUsage": {"claude-opus-4-8": {"inputTokens": "9", "outputTokens": 4, "cacheReadInputTokens": ["x"]}},
        }
    )

    sample = _adapter(spawn_executor).parse_usage(envelope, "judge")

    assert sample is not None
    counts = (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens)
    assert counts == (0, 45, 0, 0)
    assert sample.cost_scope_tokens == 13


def test_sum_transcript_usage_counts_each_empty_id_record_rather_than_collapsing_them(
    spawn_executor: Executor,
) -> None:
    lines = [transcript_fixtures.assistant_usage(input_tokens=4, message_id="") for _ in range(2)]

    assert _adapter(spawn_executor).sum_transcript_usage(lines, "spawn").input_tokens == 8


def test_sum_transcript_usage_of_empty_transcript_is_zeroed(spawn_executor: Executor) -> None:
    sample = _adapter(spawn_executor, model="claude-sonnet-5").sum_transcript_usage([], "judge")

    assert sample.kind == "judge"
    assert sample.model == "unknown"  # nothing to read — no observed model
    assert sample.input_tokens == 0
    assert sample.cost_usd is None


def test_observed_model_reads_the_last_assistant_records_model(spawn_executor: Executor) -> None:
    def _record(model: str | None) -> str:
        message: dict[str, object] = {"role": "assistant", "usage": {"input_tokens": 1, "output_tokens": 1}}
        if model is not None:
            message["model"] = model
        return json.dumps({"type": "assistant", "message": message})

    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
        _record("claude-opus-5"),
        _record(None),
        _record("claude-sonnet-5"),
    ]

    assert _adapter(spawn_executor, model="claude-haiku-4-5").observed_model(lines) == "claude-sonnet-5"


def test_observed_model_is_none_when_no_record_names_a_model(spawn_executor: Executor) -> None:
    """Never the configured default: a caller must be able to tell "observed nothing"
    apart from "observed the fallback", which `sum_transcript_usage`'s own model cannot."""
    lines = [
        "",
        "not json",
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
        json.dumps({"type": "assistant", "message": "not-a-dict"}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "model": "", "usage": {}}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "usage": {"input_tokens": 3}}}),
    ]
    adapter = _adapter(spawn_executor, model="claude-sonnet-5")

    assert adapter.observed_model(lines) is None
    assert adapter.observed_model([]) is None


# --------------------------------------------------------------------------- #
# Injected per-lease stdout redirect (epic #57): spawn / resume_with_message


def test_resolve_model_maps_the_standard_tiers_with_no_config_at_all(spawn_executor: Executor) -> None:
    # A zero-config runner resolves the three standard tiers off the adapter's built-ins.
    adapter = _adapter(spawn_executor, binary="claude")
    assert adapter.resolve_model(["blizzard:frontier"]) == "fable"
    assert adapter.resolve_model(["blizzard:advanced"]) == "opus"
    assert adapter.resolve_model(["blizzard:basic"]) == "sonnet"


def test_resolve_model_takes_the_first_entry_that_resolves(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    assert adapter.resolve_model(["blizzard:advanced", "blizzard:basic"]) == "opus"


def test_resolve_model_skips_a_native_name_belonging_to_another_harness(spawn_executor: Executor) -> None:
    # A mixed list must fall past a name belonging to another harness rather than hand
    # `claude` a name it would reject, turning a preference into a spawn failure.
    adapter = _adapter(spawn_executor, binary="claude")
    assert adapter.resolve_model(["gpt-5.3-codex", "blizzard:basic"]) == "sonnet"


def test_resolve_model_skips_an_alias_neither_config_nor_builtins_map(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    assert adapter.resolve_model(["blizzard:experimental", "blizzard:basic"]) == "sonnet"


def test_resolve_model_accepts_a_native_short_name_and_the_claude_family_prefix(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    assert adapter.resolve_model(["haiku"]) == "haiku"
    assert adapter.resolve_model(["claude-sonnet-5"]) == "claude-sonnet-5"


def test_runner_config_overrides_the_adapters_builtin_tier(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", model_aliases=(("blizzard:basic", "haiku"),))
    assert adapter.resolve_model(["blizzard:basic"]) == "haiku"


def test_an_all_unresolvable_list_falls_back_to_the_adapter_default_with_a_note(spawn_executor: Executor) -> None:
    # Never a spawn failure: an all-unresolvable list is exactly what a mixed-harness
    # fleet produces, and the fallback says which entries it skipped.
    adapter = _adapter(spawn_executor, binary="claude", model="claude-opus-5")

    with capture_logs() as logs:
        resolved = adapter.resolve_model(["gpt-5.3-codex", "blizzard:experimental"])

    assert resolved == "claude-opus-5"
    note = next(entry for entry in logs if "falling back" in entry["event"])
    assert note["skipped"] == ["gpt-5.3-codex", "blizzard:experimental"]
    assert note["fallback"] == "claude-opus-5"


def test_an_empty_preference_list_is_the_adapter_default(spawn_executor: Executor) -> None:
    # A chunk that expresses no preference.
    assert _adapter(spawn_executor, binary="claude", model="claude-opus-5").resolve_model([]) == "claude-opus-5"


def test_resolve_model_strict_mirrors_resolve_model_when_something_resolves(spawn_executor: Executor) -> None:
    # The same left-to-right walk, same winner.
    adapter = _adapter(spawn_executor, binary="claude")
    assert adapter.resolve_model_strict(["blizzard:advanced", "blizzard:basic"]) == "opus"


def test_resolve_model_strict_is_none_for_an_empty_list(spawn_executor: Executor) -> None:
    # No adapter-default fallback — `resolve_model`'s own contract, not this one's, which
    # reports "nothing authored resolved" instead.
    assert _adapter(spawn_executor, binary="claude", model="claude-opus-5").resolve_model_strict([]) is None


def test_resolve_model_strict_is_none_when_nothing_resolves(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", model="claude-opus-5")
    assert adapter.resolve_model_strict(["gpt-5.3-codex", "blizzard:experimental"]) is None


# --------------------------------------------------------------------------- #
# `resolvable_tier_ids`: the capability snapshot's own tier enumeration.


def test_resolvable_tier_ids_names_every_built_in_tier_with_no_config_at_all(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    assert set(adapter.resolvable_tier_ids()) == {"blizzard:frontier", "blizzard:advanced", "blizzard:basic"}


def test_resolvable_tier_ids_merges_an_operator_alias_overriding_a_built_in_once(spawn_executor: Executor) -> None:
    # The operator's own table overrides the built-in entry by key — the overridden id
    # still appears exactly once, never twice with two different native names.
    adapter = _adapter(spawn_executor, binary="claude", model_aliases=(("blizzard:basic", "haiku"),))
    tiers = adapter.resolvable_tier_ids()
    assert tiers.count("blizzard:basic") == 1
    assert set(tiers) == {"blizzard:frontier", "blizzard:advanced", "blizzard:basic"}


def test_resolvable_tier_ids_includes_an_operator_alias_outside_the_built_ins(spawn_executor: Executor) -> None:
    adapter = _adapter(
        spawn_executor=spawn_executor, binary="claude", model_aliases=(("blizzard:experimental", "opus"),)
    )
    assert "blizzard:experimental" in adapter.resolvable_tier_ids()


@pytest.mark.parametrize("value", ["low", "medium", "high", "max"])
def test_resolve_effort_passes_the_well_known_ordinal_through(value: str, spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor, binary="claude").resolve_effort(value) == value


def test_resolve_effort_reaches_a_native_tier_outside_the_ordinal_via_config(spawn_executor: Executor) -> None:
    # `xhigh` is Claude Code's own, outside the well-known four — reachable by alias.
    adapter = _adapter(spawn_executor, binary="claude", effort_aliases=(("max", "xhigh"),))
    assert adapter.resolve_effort("max") == "xhigh"


def test_resolve_effort_of_no_preference_is_none(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor, binary="claude").resolve_effort(None) is None


def test_an_unrecognized_effort_logs_once_and_is_ignored(spawn_executor: Executor) -> None:
    # An unrecognized effort is an authoring mistake — dropped rather than failing a
    # spawn, and noted once rather than on every spawn of a long-lived runner.
    adapter = _adapter(spawn_executor, binary="claude")

    with capture_logs() as logs:
        assert adapter.resolve_effort("glacial") is None
        assert adapter.resolve_effort("glacial") is None

    assert len([entry for entry in logs if "unrecognized effort" in entry["event"]]) == 1


@pytest.mark.parametrize("value", ["auto", "500k", "200000", "200"])
def test_resolve_compaction_window_passes_a_recognized_spelling_through(value: str, spawn_executor: Executor) -> None:
    # `auto` or a token count — the adapter checks the shape, not the CLI's
    # own 100k-1M range, which it never re-validates.
    assert _adapter(spawn_executor, binary="claude").resolve_compaction_window(value) == value


def test_resolve_compaction_window_of_no_preference_is_none(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor, binary="claude").resolve_compaction_window(None) is None


@pytest.mark.parametrize("value", ["", "150k tokens", "True", "auto2"])
def test_an_unrecognized_compaction_window_logs_once_per_value_and_is_ignored(
    value: str, spawn_executor: Executor
) -> None:
    adapter = _adapter(spawn_executor, binary="claude")

    with capture_logs() as logs:
        assert adapter.resolve_compaction_window(value) is None
        assert adapter.resolve_compaction_window(value) is None

    assert len([entry for entry in logs if "unrecognized compaction window" in entry["event"]]) == 1


def test_a_missing_compaction_window_is_silently_none_never_logged(spawn_executor: Executor) -> None:
    # `None` means "no declaration" — not an authoring mistake, so it never logs (unlike
    # a real bad value, or the empty string): mirrors `resolve_effort`'s treatment of `None`.
    adapter = _adapter(spawn_executor, binary="claude")

    with capture_logs() as logs:
        assert adapter.resolve_compaction_window(None) is None

    assert not [entry for entry in logs if "compaction window" in entry["event"]]


# The session's resolved model and effort are both reasserted on every invocation.


def test_spawn_at_mint_carries_the_resolved_model_and_effort(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    adapter.spawn(envelope, preamble, session_hint="sid", model="sonnet", effort="high")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_spawn_on_a_resume_reasserts_the_session_model_and_effort(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    adapter.spawn(envelope, preamble, session_hint="sid", resume_from="prior", model="sonnet", effort="high")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"  # not sticky, so reasserted


def test_spawn_at_mint_carries_the_compaction_window(monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    adapter.spawn(envelope, preamble, session_hint="sid", model="sonnet", compaction_window="150k")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--autocompact") + 1] == "150k"


def test_spawn_on_a_resume_reasserts_the_compaction_window(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    # Reasserted like effort, never sticky-by-omission.
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    adapter.spawn(envelope, preamble, session_hint="sid", resume_from="prior", compaction_window="150k")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--autocompact") + 1] == "150k"


def test_spawn_supplying_no_compaction_window_omits_the_flag(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    adapter.spawn(envelope, preamble, session_hint="sid")

    cmd = captured["cmd"]
    assert "--autocompact" not in cmd


def test_judge_carries_the_compaction_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))

    _adapter(spawn_executor, binary="claude").judge(
        "/ws", "sid", "verdict?", str(tmp_path / "judge-output.txt"), compaction_window="150k"
    )

    cmd = captured["cmd"]
    assert cmd[cmd.index("--autocompact") + 1] == "150k"


def test_resume_with_message_carries_the_compaction_window(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))

    _adapter(spawn_executor, binary="claude").resume_with_message("/ws", "sid", "msg", compaction_window="150k")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--autocompact") + 1] == "150k"


def test_spawn_supplying_neither_behaves_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture(spawn_executor)

    adapter.spawn(envelope, preamble, session_hint="sid")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"  # the adapter's own default
    assert "--effort" not in cmd


def test_judge_reasserts_the_session_model_and_effort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))

    _adapter(spawn_executor, binary="claude").judge(
        "/ws", "sid", "verdict?", str(tmp_path / "judge-output.txt"), effort="high", model="sonnet"
    )

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_resume_with_message_reasserts_the_session_model_and_effort(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))

    _adapter(spawn_executor, binary="claude").resume_with_message("/ws", "sid", "msg", model="sonnet", effort="high")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"


# Usage attribution: the caller supplies the model resolved for an
# invocation the harness itself reports none for.


def test_parse_usage_does_not_attribute_an_unobserved_model_to_the_expectation(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", model="claude-opus-5")
    output = json.dumps({"result": "x", "usage": {"input_tokens": 5}})

    sample = adapter.parse_usage(output, "spawn", model="sonnet")

    assert sample is not None
    assert sample.model == "unknown"


def test_parse_usage_still_prefers_what_the_harness_itself_reports(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    output = json.dumps({"result": "x", "model": "claude-haiku-4-5", "usage": {"input_tokens": 5}})

    sample = adapter.parse_usage(output, "spawn", model="sonnet")

    assert sample is not None
    assert sample.model == "claude-haiku-4-5"


def test_parse_usage_without_an_observed_model_does_not_assume_the_adapter_default(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", model="claude-opus-5")
    output = json.dumps({"result": "x", "usage": {"input_tokens": 5}})

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.model == "unknown"


def test_parse_usage_attributes_envelope_without_model_to_this_invocations_transcript(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", model="fable")
    output = json.dumps(
        {
            "type": "result",
            "usage": {"input_tokens": 5, "cache_read_input_tokens": 100},
            "total_cost_usd": 0.2,
            "modelUsage": {
                "claude-opus-5": {"inputTokens": 20},
                "claude-fable-5-1": {"inputTokens": 5},
            },
        }
    )
    lines = [json.dumps({"type": "assistant", "message": {"model": "claude-fable-5-1"}})]

    with capture_logs() as logs:
        sample = adapter.parse_usage(output, "judge", model="claude-opus-5", transcript_lines=lines)

    assert sample is not None
    assert sample.model == "claude-fable-5-1"
    assert sample.cost_usd == 0.2
    assert any(
        log["event"] == "harness usage model differs from session model" and log["observed_model"] == "claude-fable-5-1"
        for log in logs
    )


def test_parse_usage_ignores_an_inline_sidechains_model_after_the_worker_reply(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    output = json.dumps(
        {
            "type": "result",
            "usage": {"input_tokens": 5},
            "modelUsage": {"claude-sonnet-5": {"inputTokens": 5}, "claude-haiku-4-5": {"inputTokens": 2}},
        }
    )
    lines = [
        transcript_fixtures.assistant_usage(model="claude-sonnet-5", message_id="worker"),
        transcript_fixtures.assistant_usage(model="claude-haiku-4-5", message_id="subagent", sidechain=True),
    ]

    assert adapter.needs_usage_transcript(output)
    sample = adapter.parse_usage(output, "judge", model="sonnet", transcript_lines=lines)
    assert sample is not None
    assert sample.model == "claude-sonnet-5"
    assert adapter.sum_transcript_usage(lines, "spawn", model="sonnet").model == "claude-sonnet-5"


def test_parse_usage_uses_a_single_model_usage_entry_when_no_transcript_is_available(spawn_executor: Executor) -> None:
    output = json.dumps(
        {"type": "result", "usage": {"input_tokens": 5}, "modelUsage": {"claude-sonnet-5": {"inputTokens": 5}}}
    )

    sample = _adapter(spawn_executor).parse_usage(output, "judge", model="fable")

    assert sample is not None
    assert sample.model == "claude-sonnet-5"


def test_sum_transcript_usage_keeps_model_unknown_when_no_line_names_one(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", model="claude-opus-5")
    lines = [json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 3}}})]

    with capture_logs() as logs:
        sample = adapter.sum_transcript_usage(lines, "judge", model="sonnet")

    assert sample.model == "unknown"
    assert sample.input_tokens == 3
    unavailable = [e for e in logs if e["event"] == "harness usage model unavailable"]
    assert [(e["log_level"], e["expected_model"], e["kind"]) for e in unavailable] == [("warning", "sonnet", "judge")]


def test_sum_transcript_usage_takes_a_supplied_observation_without_re_deriving_it(
    spawn_executor: Executor, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    lines = [transcript_fixtures.assistant_usage(model="claude-sonnet-5", message_id="m1")]

    def _refuse_rederiving(lines: Sequence[str]) -> str | None:
        pytest.fail("re-derived a supplied observation")

    monkeypatch.setattr(adapter, "observed_model", _refuse_rederiving)

    sample = adapter.sum_transcript_usage(lines, "spawn", model="sonnet", observed="claude-fable-5-1")

    # The supplied observation wins over what the lines would derive, and the pinned alias never stands in.
    assert sample.model == "claude-fable-5-1"


def test_sum_transcript_usage_derives_the_observation_from_its_lines_when_none_is_supplied(
    spawn_executor: Executor,
) -> None:
    adapter = _adapter(spawn_executor, binary="claude", model="claude-opus-5")
    lines = [transcript_fixtures.assistant_usage(model="claude-sonnet-5", message_id="m1")]

    with capture_logs() as logs:
        sample = adapter.sum_transcript_usage(lines, "spawn", model="sonnet")

    # A lease pinned to the `sonnet` alias still records the model the transcript shows ran.
    assert sample.model == "claude-sonnet-5"
    assert sample.kind == "spawn"
    # The alias names what ran, so nothing differs and nothing is unavailable.
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == []


def test_sum_transcript_usage_compares_a_supplied_observation_against_the_expected_model(
    spawn_executor: Executor,
) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    lines = [transcript_fixtures.assistant_usage(model="claude-sonnet-5", message_id="m1")]

    with capture_logs() as logs:
        adapter.sum_transcript_usage(lines, "spawn", model="claude-sonnet-5", observed="claude-fable-5-1")

    differs = [e for e in logs if e["event"] == "harness usage model differs from session model"]
    assert [(e["expected_model"], e["observed_model"]) for e in differs] == [("claude-sonnet-5", "claude-fable-5-1")]


def test_the_base_allowlist_carries_no_anthropic_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A leaked model override can move a resumed worker off its session's model."""
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-haiku-4-5")

    env = AllowlistedEnv.of(()).variables

    assert not [name for name in env if name.startswith("ANTHROPIC_")]


# resume_command composes a command for an attended takeover.


def test_resume_command_appends_the_sessions_stamped_model_and_effort(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")

    command = adapter.resume_command("/ws/e1", "sess-a", model="opus", effort="high")

    assert command == "cd /ws/e1 && claude --resume sess-a --model opus --effort high"


def test_resume_command_with_no_stamps_renders_todays_bare_command(spawn_executor: Executor) -> None:
    # A session predating the stamps reads *unknown*: render the bare command rather than
    # guessing at a default and presenting it as what the session ran.
    adapter = _adapter(spawn_executor, binary="claude")

    assert adapter.resume_command("/ws/e1", "sess-a") == "cd /ws/e1 && claude --resume sess-a"


def test_resume_command_appends_only_the_stamp_it_has(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")

    assert (
        adapter.resume_command("/ws/e1", "sess-a", model="opus") == "cd /ws/e1 && claude --resume sess-a --model opus"
    )
    assert (
        adapter.resume_command("/ws/e1", "sess-a", effort="high") == "cd /ws/e1 && claude --resume sess-a --effort high"
    )


def test_parse_usage_reads_the_cost_scope_off_the_model_breakdown(spawn_executor: Executor) -> None:
    # Claude Code's own shape: `usage` is this invocation, `modelUsage` is what the cost
    # figure was charged for — and on a resumed session that is the session to date.
    envelope = json.dumps(
        {
            "type": "result",
            "result": "ok",
            "session_id": "s1",
            "usage": {
                "input_tokens": 4,
                "output_tokens": 1929,
                "cache_read_input_tokens": 168903,
                "cache_creation_input_tokens": 1494,
            },
            "total_cost_usd": 2.118976,
            "modelUsage": {
                "claude-opus-5": {
                    "inputTokens": 56,
                    "outputTokens": 21630,
                    "cacheReadInputTokens": 1687112,
                    "cacheCreationInputTokens": 73439,
                    "costUSD": 2.118976,
                }
            },
        }
    )
    sample = _adapter(spawn_executor).parse_usage(envelope, "resume")
    assert sample is not None
    assert sample.token_total == 172330  # this invocation's own four counts
    assert sample.cost_scope_tokens == 1782237  # what $2.118976 was charged for
    assert sample.cost_usd == 2.118976


def test_parse_usage_sums_every_model_the_breakdown_names(spawn_executor: Executor) -> None:
    # Sub-models the top-level `usage` omits are still inside the cost figure's scope.
    envelope = json.dumps(
        {
            "type": "result",
            "result": "ok",
            "session_id": "s1",
            "usage": {"input_tokens": 1, "output_tokens": 3, "cache_read_input_tokens": 0},
            "total_cost_usd": 0.0314736,
            "modelUsage": {
                "claude-sonnet-5": {"inputTokens": 1, "outputTokens": 3, "cacheReadInputTokens": 18538},
                "claude-haiku-4-5-20251001": {"inputTokens": 0, "outputTokens": 9},
            },
        }
    )
    sample = _adapter(spawn_executor).parse_usage(envelope, "spawn")
    assert sample is not None
    assert sample.cost_scope_tokens == 18551


def test_parse_usage_reports_no_cost_scope_when_the_envelope_breaks_out_no_models(spawn_executor: Executor) -> None:
    # The shape that preceded the breakdown: nothing to read, so the figure is this
    # invocation's and rides verbatim.
    sample = _adapter(spawn_executor).parse_usage(_USAGE_ENVELOPE, "judge")
    assert sample is not None
    assert sample.cost_scope_tokens is None


# --- classify_usage_limit ------------------------------------


def test_classify_usage_limit_reads_the_synthetic_transcript_record(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)  # 2:00pm America/Chicago (CDT, UTC-5)
    lines = [transcript_fixtures.rate_limit_record()]
    limit = _adapter(spawn_executor).classify_usage_limit("", lines, now)
    assert limit is not None
    assert limit.detail == "You've hit your session limit · resets 5:40pm (America/Chicago)"
    # 5:40pm CDT the same day is 22:40 UTC.
    assert limit.resets_at == datetime(2026, 9, 5, 22, 40, tzinfo=UTC)


def test_classify_usage_limit_rolls_the_reset_to_the_next_day_when_already_past(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 23, 0, tzinfo=UTC)  # past 5:40pm CDT already
    limit = _adapter(spawn_executor).classify_usage_limit("", [transcript_fixtures.rate_limit_record()], now)
    assert limit is not None
    assert limit.resets_at == datetime(2026, 9, 6, 22, 40, tzinfo=UTC)


def test_classify_usage_limit_none_for_an_unparseable_reset_never_raises(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    record = transcript_fixtures.rate_limit_record(text="You've hit your session limit")
    limit = _adapter(spawn_executor).classify_usage_limit("", [record], now)
    assert limit is not None
    assert limit.resets_at is None
    assert limit.detail == "You've hit your session limit"


def test_classify_usage_limit_none_for_an_unrecognized_timezone(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    record = transcript_fixtures.rate_limit_record(text="resets 5:40pm (Nowhere/Imaginary)")
    limit = _adapter(spawn_executor).classify_usage_limit("", [record], now)
    assert limit is not None
    assert limit.resets_at is None


def test_classify_usage_limit_is_none_for_an_ordinary_assistant_reply(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    limit = _adapter(spawn_executor).classify_usage_limit(
        "", [transcript_fixtures.assistant_text("all good here")], now
    )
    assert limit is None


def test_classify_usage_limit_is_none_for_a_non_rate_limit_api_error(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    other_error = json.dumps({"type": "assistant", "isApiErrorMessage": True, "error": "overloaded"})
    limit = _adapter(spawn_executor).classify_usage_limit("", [other_error], now)
    assert limit is None


def test_classify_usage_limit_tolerates_malformed_lines(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    limit = _adapter(spawn_executor).classify_usage_limit(
        "", ["not json", "", transcript_fixtures.rate_limit_record()], now
    )
    assert limit is not None


# --- classify_provider_overload ------------------------------


def test_classify_provider_overload_reads_the_synthetic_transcript_record(spawn_executor: Executor) -> None:
    overload = _adapter(spawn_executor).classify_provider_overload("", [transcript_fixtures.overload_record()])
    assert overload is not None
    assert "529" in overload.detail


def test_classify_provider_overload_is_none_for_an_ordinary_assistant_reply(spawn_executor: Executor) -> None:
    lines = [transcript_fixtures.assistant_text("all good here")]
    assert _adapter(spawn_executor).classify_provider_overload("", lines) is None


def test_classify_provider_overload_is_none_for_the_sigint_error_during_execution_envelope(
    spawn_executor: Executor,
) -> None:
    # The envelope-shaped output plays no part — only the transcript range does, and a
    # SIGINT'd-but-completed turn's own last transcript record is an ordinary reply.
    output = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True})
    lines = [transcript_fixtures.assistant_text("partial work before the interrupt")]
    assert _adapter(spawn_executor).classify_provider_overload(output, lines) is None


def test_classify_provider_overload_is_none_for_a_rate_limit_api_error(spawn_executor: Executor) -> None:
    lines = [transcript_fixtures.rate_limit_record()]
    assert _adapter(spawn_executor).classify_provider_overload("", lines) is None


def test_classify_provider_overload_is_none_for_a_non_overload_server_error(spawn_executor: Executor) -> None:
    other_error = json.dumps({"type": "assistant", "isApiErrorMessage": True, "error": "server_error"})
    assert _adapter(spawn_executor).classify_provider_overload("", [other_error]) is None


def test_classify_provider_overload_is_none_when_a_real_reply_follows_it_in_the_range(spawn_executor: Executor) -> None:
    # A relaunched judge reuses its first judge boundary, so its own range can hold an
    # earlier overload record followed by a real reply — that generation completed.
    lines = [transcript_fixtures.overload_record(), transcript_fixtures.assistant_text("here is my verdict")]
    assert _adapter(spawn_executor).classify_provider_overload("", lines) is None


def test_classify_provider_overload_tolerates_malformed_lines(spawn_executor: Executor) -> None:
    lines = ["not json", "", transcript_fixtures.overload_record()]
    assert _adapter(spawn_executor).classify_provider_overload("", lines) is not None


def test_spawn_never_splices_graph_artifact_content_into_the_prompt(
    monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, _, preamble = _spawn_fixture(spawn_executor)
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=[("pass", "ok")],
        graph_artifacts=[GraphArtifact(name="rubric", kind=ArtifactKind.ASSET, content="RUBRIC-BODY-7f3a")],
    )

    adapter.spawn(envelope, preamble, session_hint="fresh-hint").await_identity(0)

    cmd = captured["cmd"]
    assert envelope.prompt in cmd
    assert not any("RUBRIC-BODY-7f3a" in part or "rubric" in part for part in cmd)


def test_a_declared_supported_list_refuses_a_native_name_outside_it(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", supported_models=["opus"])
    assert adapter.resolve_model_strict(["sonnet", "claude-sonnet-4"]) is None
    assert adapter.resolve_model_strict(["sonnet", "opus"]) == "opus"


def test_a_declared_supported_pattern_accepts_a_matching_native_name(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", supported_models=["claude-opus-*"])
    assert adapter.resolve_model_strict(["claude-opus-5"]) == "claude-opus-5"
    assert adapter.resolve_model_strict(["opus"]) is None


def test_an_empty_supported_list_accepts_no_native_name_but_still_maps_a_tier(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude", supported_models=[])
    assert adapter.resolve_model_strict(["opus"]) is None
    assert adapter.resolve_model_strict(["blizzard:advanced"]) == "opus"


def test_an_alias_target_bypasses_the_supported_list(spawn_executor: Executor) -> None:
    adapter = _adapter(
        spawn_executor, binary="claude", supported_models=["haiku"], model_aliases=[("house", "gpt-custom")]
    )
    assert adapter.resolve_model_strict(["house"]) == "gpt-custom"


def test_with_no_declared_list_the_built_in_vocabulary_is_accepted_and_reported(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="claude")
    assert adapter.resolve_model_strict(["fable"]) == "fable"
    assert adapter.resolve_model_strict(["claude-opus-5"]) == "claude-opus-5"
    assert adapter.resolve_model_strict(["gpt-5"]) is None
    assert adapter.supported_models() == ("fable", "opus", "sonnet", "haiku", "claude-*")


def test_orphan_and_reconcile_touch_nothing_a_worker_that_owned_its_turn_took_with_it(spawn_executor: Executor) -> None:
    probe = FakeProbe()
    adapter = ClaudeCodeAdapter(
        binary="claude",
        worker_env=AllowlistedEnv.of(()),
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor, children=CHILDREN),
        versions=version_probe(),
    )
    adapter.orphan("ses_1")
    assert adapter.reconcile(("ses_1", "ses_2")) == 0
    assert probe.killed == [] and probe.killed_groups == [] and probe.interrupted_groups == []
