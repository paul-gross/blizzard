"""The Claude Code adapter — verdict parsing (unit) and a real subprocess (component).

``parse_verdict`` is exercised over the harness-native JSON envelope and its failure
modes; the component test drives spawn/resume against a real fake-harness binary. Also
covers usage parsing, the per-lease stdout redirect, and node-entry resume (issue #115).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.wire.envelope import NodeEnvelope
from tests.conftest import _WORKER_IDENTITY_ENV
from tests.runner_fakes import make_envelope

_JSON_PASS = '{"type":"result","subtype":"success","is_error":false,"result":"Looks good. <Choice>pass</Choice>","session_id":"s1"}'


@pytest.mark.unit
def test_parse_verdict_extracts_choice_from_json_envelope() -> None:
    assert ClaudeCodeAdapter().parse_verdict(_JSON_PASS) == "pass"


@pytest.mark.unit
def test_parse_verdict_reads_plain_text_reply() -> None:
    assert ClaudeCodeAdapter().parse_verdict("verdict: <Choice>fail</Choice>") == "fail"


@pytest.mark.unit
def test_parse_verdict_missing_choice_is_none() -> None:
    assert ClaudeCodeAdapter().parse_verdict('{"type":"result","result":"no verdict here","session_id":"s1"}') is None
    # A delivered judgement whose reply has no parseable <Choice> is a failure:
    # an unclosed tag, whitespace-only name, and a bare open tag all read as None.
    assert ClaudeCodeAdapter().parse_verdict("<Choice>") is None
    assert ClaudeCodeAdapter().parse_verdict("<Choice></Choice>") is None
    assert ClaudeCodeAdapter().parse_verdict("<Choice>   </Choice>") is None


@pytest.mark.unit
def test_parse_assessment_returns_text_after_the_choice() -> None:
    output = '{"type":"result","result":"<Choice>fail</Choice>\\nBLOCKING: guard empty input","session_id":"s1"}'
    assert ClaudeCodeAdapter().parse_assessment(output) == "BLOCKING: guard empty input"


@pytest.mark.unit
def test_parse_assessment_is_empty_without_a_choice() -> None:
    assert ClaudeCodeAdapter().parse_assessment("no verdict at all") == ""


@pytest.mark.unit
def test_resume_command_is_the_literal_takeover() -> None:
    cmd = ClaudeCodeAdapter(binary="claude").resume_command("/ws/e1", "sess-x")
    assert cmd == "cd /ws/e1 && claude --resume sess-x"


@pytest.mark.unit
def test_attended_resume_command_reasserts_the_permission_mode() -> None:
    # The flag is per-invocation, not session-sticky (issue #258): the takeover door's
    # exec'd command reasserts it so a bypassPermissions worker is not demoted mid-task.
    adapter = ClaudeCodeAdapter(binary="claude", permission_mode="bypassPermissions")

    cmd = adapter.resume_command("/ws/e1", "sess-x", model="opus", attended=True)

    assert cmd == "cd /ws/e1 && claude --resume sess-x --model opus --permission-mode bypassPermissions"


@pytest.mark.unit
def test_the_advertised_paste_string_never_carries_the_permission_mode() -> None:
    # The default composition is the escalation record / `runner status` paste string:
    # a human runs it in a bare terminal, so it stays at the interactive default.
    adapter = ClaudeCodeAdapter(binary="claude", permission_mode="bypassPermissions")

    assert adapter.resume_command("/ws/e1", "sess-x") == "cd /ws/e1 && claude --resume sess-x"


@pytest.mark.unit
def test_resume_command_without_a_permission_mode_stays_bare() -> None:
    assert (
        ClaudeCodeAdapter(binary="claude").resume_command("/ws/e1", "sess-x", attended=True)
        == "cd /ws/e1 && claude --resume sess-x"
    )


# --------------------------------------------------------------------------- #
# Node-entry resume (issue #115): the CLI flag branch, with ``subprocess.Popen`` faked.


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


def _spawn_fixture() -> tuple[ClaudeCodeAdapter, NodeEnvelope, WorkerPreamble]:
    adapter = ClaudeCodeAdapter(binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )
    return adapter, envelope, preamble


@pytest.mark.unit
def test_spawn_with_resume_from_emits_resume_flag_and_echoes_its_continuation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture()

    handle = adapter.spawn(envelope, preamble, session_hint="fresh-hint", resume_from="prior-sid")

    cmd = captured["cmd"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "prior-sid"
    assert "--session-id" not in cmd
    # In-place (Q1): the continuation stays under the resumed id, not the hint.
    assert handle.session_id == "prior-sid"


@pytest.mark.unit
def test_spawn_without_resume_from_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture()

    handle = adapter.spawn(envelope, preamble, session_hint="fresh-hint")

    cmd = captured["cmd"]
    assert "--session-id" in cmd
    assert cmd[cmd.index("--session-id") + 1] == "fresh-hint"
    assert "--resume" not in cmd
    assert handle.session_id == "fresh-hint"


# --------------------------------------------------------------------------- #
# Spawn-environment allowlist (issue #88): no call path copies `os.environ` wholesale

_SENTINEL_UNLISTED_VAR = "MY_UNLISTED_SENTINEL_VAR"


@pytest.mark.unit
def test_spawn_env_excludes_the_hub_token_and_an_unlisted_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN", "super-secret-token")
    monkeypatch.setenv(_SENTINEL_UNLISTED_VAR, "should-not-leak")
    adapter = ClaudeCodeAdapter(binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter._spawn_env(envelope, preamble, "sess-1")

    assert "BZ_HUB_TOKEN" not in env
    assert _SENTINEL_UNLISTED_VAR not in env


@pytest.mark.component
def test_judge_child_env_excludes_the_hub_token_and_an_unlisted_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN", "super-secret-token")
    monkeypatch.setenv(_SENTINEL_UNLISTED_VAR, "should-not-leak")
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=str(dump_script))

    adapter.judge(str(workdir), "sess-1", "assess")

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert "BZ_HUB_TOKEN" not in dumped
    assert _SENTINEL_UNLISTED_VAR not in dumped


@pytest.mark.component
def test_judge_injects_the_lease_identity_when_given_a_preamble(tmp_path: Path) -> None:
    # `--resume` inherits none of the spawn env, so a judge given a preamble must carry
    # the same per-lease identity a resume does.
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=str(dump_script))
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_42",
        local_api_url="http://127.0.0.1:8431",
        lease_token="fresh-judge-token",
    )

    adapter.judge(str(workdir), "sess-9", "assess", preamble=preamble, chunk_id="ch_9")

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert dumped["BLIZZARD_LEASE_ID"] == "lease_42"
    assert dumped["BLIZZARD_RUNNER_URL"] == "http://127.0.0.1:8431"
    assert dumped["BLIZZARD_LEASE_TOKEN"] == "fresh-judge-token"  # the re-minted plaintext rides the judge
    assert dumped["BLIZZARD_CHUNK_ID"] == "ch_9"


@pytest.mark.component
def test_resume_with_message_child_env_excludes_the_hub_token_and_an_unlisted_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN", "super-secret-token")
    monkeypatch.setenv(_SENTINEL_UNLISTED_VAR, "should-not-leak")
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=str(dump_script))

    pid = adapter.resume_with_message(str(workdir), "sess-1", "deliver")
    os.waitpid(pid, 0)

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert "BZ_HUB_TOKEN" not in dumped
    assert _SENTINEL_UNLISTED_VAR not in dumped


@pytest.mark.component
def test_resume_with_message_injects_the_lease_identity_when_given_a_preamble(tmp_path: Path) -> None:
    # A resumed worker needs the same per-lease identity a fresh spawn gets, since
    # `--resume` inherits none of the spawn env — the resume child env must carry it too.
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=str(dump_script))
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_42",
        local_api_url="http://127.0.0.1:8431",
        lease_token="fresh-resume-token",
    )

    pid = adapter.resume_with_message(str(workdir), "sess-9", "continue", preamble=preamble, chunk_id="ch_9")
    os.waitpid(pid, 0)

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert dumped["BLIZZARD_LEASE_ID"] == "lease_42"
    assert dumped["BLIZZARD_RUNNER_URL"] == "http://127.0.0.1:8431"
    assert dumped["BLIZZARD_LEASE_TOKEN"] == "fresh-resume-token"  # the re-minted plaintext rides the resume
    assert dumped["BLIZZARD_CHUNK_ID"] == "ch_9"


@pytest.mark.unit
def test_spawn_env_forwards_a_named_passthrough_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_HARNESS_QUIRK", "needed-by-the-real-binary")
    adapter = ClaudeCodeAdapter(binary="claude", env_passthrough=("MY_HARNESS_QUIRK",))
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter._spawn_env(envelope, preamble, "sess-1")

    assert env["MY_HARNESS_QUIRK"] == "needed-by-the-real-binary"


@pytest.mark.unit
def test_spawn_env_forwards_lc_prefixed_locale_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LC_TIME", "fr_FR.UTF-8")
    adapter = ClaudeCodeAdapter(binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter._spawn_env(envelope, preamble, "sess-1")

    assert env["LC_ALL"] == "en_US.UTF-8"
    assert env["LC_TIME"] == "fr_FR.UTF-8"


@pytest.mark.unit
def test_spawn_env_still_carries_the_base_allowlist_and_deliberate_blizzard_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The allowlist is not a denylist rewrite in disguise: PATH/HOME and the adapter's
    # own BLIZZARD_* additions still ride the child env.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/worker")
    adapter = ClaudeCodeAdapter(binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    env = adapter._spawn_env(envelope, preamble, "sess-1")

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/worker"
    assert env["BLIZZARD_LEASE_ID"] == "lease_1"
    assert env["BLIZZARD_SESSION_ID"] == "sess-1"


@pytest.mark.unit
def test_spawn_env_carries_the_lease_capability_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # issue #113, Phase 1 — the preamble's plaintext lease token rides the spawn env
    # as an explicit per-spawn identity var, alongside BLIZZARD_LEASE_ID.
    adapter = ClaudeCodeAdapter(binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        lease_token="plaintext-lease-token",
    )

    env = adapter._spawn_env(envelope, preamble, "sess-1")

    assert env["BLIZZARD_LEASE_TOKEN"] == "plaintext-lease-token"


@pytest.mark.unit
def test_the_suites_worker_identity_strip_list_covers_every_var_spawn_env_injects() -> None:
    """The conftest strip-list and ``_spawn_env`` agree on the worker identity set — add
    a ``BLIZZARD_*`` var to ``_spawn_env`` without adding it to the strip-list and fail
    here, rather than only in a fleet worker where nobody is watching a red suite."""
    adapter = ClaudeCodeAdapter(binary="claude")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        lease_token="plaintext-lease-token",
    )

    injected = {k for k in adapter._spawn_env(envelope, preamble, "sess-1") if k.startswith("BLIZZARD_")}

    assert injected - set(_WORKER_IDENTITY_ENV) == set()


_ENV_DUMP_HARNESS = """#!/usr/bin/env python3
import json, os
with open("env-dump.json", "w") as f:
    json.dump(dict(os.environ), f)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "", "session_id": "auto"}))
"""


_FAKE_HARNESS = """#!/usr/bin/env python3
import sys, json
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
open("argv.txt", "w").write(" ".join(args))
if resume is None:
    open("spawned-here.txt", "w").write(prompt or "")
    result = ""
else:
    result = "Assessed. <Choice>pass</Choice>"
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result, "session_id": sid}))
"""


def _fake_binary(tmp_path: Path) -> str:
    script = tmp_path / "fake-claude"
    script.write_text(_FAKE_HARNESS)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(script)


@pytest.mark.component
def test_spawn_launches_real_process_in_workdir(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")

    assert handle.session_id == "sess-123"  # Claude honors the pre-assigned id
    assert handle.pid > 0
    assert handle.process_start_time  # stamped from /proc for pid-reuse-proof liveness
    os.waitpid(handle.pid, 0)  # let the fire-and-forget child finish
    assert (workdir / "spawned-here.txt").read_text() == (envelope.prompt or "")  # ran in the acquired workdir
    assert "--permission-mode" not in (workdir / "argv.txt").read_text()  # omitted when unset
    assert "--model claude-opus-5" in (workdir / "argv.txt").read_text()  # pinned Opus, not the ambient default


@pytest.mark.component
def test_spawn_pins_a_configured_model(tmp_path: Path) -> None:
    # The worker model is pinned so a spawn never inherits the operator's ambient
    # ``claude`` default; the constructor argument overrides the Opus default.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary, model="claude-sonnet-5")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    os.waitpid(handle.pid, 0)

    assert "--model claude-sonnet-5" in (workdir / "argv.txt").read_text()


@pytest.mark.component
def test_spawn_passes_the_permission_mode_flag_when_configured(tmp_path: Path) -> None:
    # A headless worker has no one to approve tool use; the configured permission mode is
    # what lets it edit/commit in its sandboxed worktree.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary, permission_mode="bypassPermissions")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    os.waitpid(handle.pid, 0)

    assert "--permission-mode bypassPermissions" in (workdir / "argv.txt").read_text()


@pytest.mark.component
def test_judge_resume_output_parses_to_choice(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary)

    output = adapter.judge(str(workdir), "sess-123", "Assess the build. Reply <Choice>name</Choice>.")
    assert adapter.parse_verdict(output) == "pass"


@pytest.mark.component
def test_judge_passes_the_permission_mode_flag_when_configured(tmp_path: Path) -> None:
    # ``--permission-mode`` is per-invocation, not session-sticky, so ``judge`` must
    # reassert it exactly as ``spawn``/``resume_with_message`` do.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary, permission_mode="bypassPermissions")

    adapter.judge(str(workdir), "sess-123", "Assess. Reply <Choice>name</Choice>.")

    assert "--permission-mode bypassPermissions" in (workdir / "argv.txt").read_text()


@pytest.mark.component
def test_resume_with_message_carries_the_worker_settings_hooks(tmp_path: Path) -> None:
    # ``--resume`` does not inherit the spawn's ``--settings``, so a resumed session
    # would lose its heartbeat/session-end hooks unless this re-attaches the file.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    settings = tmp_path / "worker-settings.json"
    adapter = ClaudeCodeAdapter(binary=binary, settings_path=str(settings))

    pid = adapter.resume_with_message(str(workdir), "sess-123", "continue where you left off")
    os.waitpid(pid, 0)

    assert f"--settings {settings}" in (workdir / "argv.txt").read_text()


@pytest.mark.component
def test_judge_omits_the_worker_settings_hooks(tmp_path: Path) -> None:
    # ``judge``'s exit is not the worker declaring done; attaching ``SessionEnd`` here
    # would record a spurious session-end for the still-live lease.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    settings = tmp_path / "worker-settings.json"
    adapter = ClaudeCodeAdapter(binary=binary, settings_path=str(settings))

    adapter.judge(str(workdir), "sess-123", "Assess. Reply <Choice>name</Choice>.")

    assert "--settings" not in (workdir / "argv.txt").read_text()


@pytest.mark.component
def test_spawn_runs_at_workspace_root_and_prepends_prefix(tmp_path: Path) -> None:
    # The worker's cwd is the winter workspace root, not the env subdir (issue #17), and the
    # runner-composed preamble is prepended to the node envelope prompt.
    binary = _fake_binary(tmp_path)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_workdir = workspace_root / "r1"
    env_workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="r1", workdir=str(env_workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        workspace_root=str(workspace_root),
        prompt_prefix="PREAMBLE-TABLE",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    os.waitpid(handle.pid, 0)

    # Ran at the workspace root — the marker file the fake writes lands there, not the env dir.
    assert (workspace_root / "spawned-here.txt").exists()
    assert not (env_workdir / "spawned-here.txt").exists()
    # The composed prompt is the prefix followed by the envelope prompt.
    assert (workspace_root / "spawned-here.txt").read_text() == f"PREAMBLE-TABLE\n\n{envelope.prompt}"


@pytest.mark.component
def test_spawn_falls_back_to_env_workdir_without_a_workspace_root(tmp_path: Path) -> None:
    # An empty workspace_root keeps the legacy cwd (the first env's workdir).
    binary = _fake_binary(tmp_path)
    env_workdir = tmp_path / "r1"
    env_workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="r1", workdir=str(env_workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    os.waitpid(handle.pid, 0)

    # No prefix and no workspace root: cwd is the env workdir, prompt is the envelope prompt alone.
    assert (env_workdir / "spawned-here.txt").read_text() == (envelope.prompt or "")


# --------------------------------------------------------------------------- #
# Usage extraction (epic #57, phase 1 of #58): parse_usage, sum_transcript_usage

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


@pytest.mark.unit
def test_parse_usage_extracts_tokens_and_cost_from_json_envelope() -> None:
    sample = ClaudeCodeAdapter().parse_usage(_USAGE_ENVELOPE, "judge")
    assert sample is not None
    assert sample.kind == "judge"
    assert sample.model == "claude-opus-4-8"
    assert sample.input_tokens == 120
    assert sample.output_tokens == 45
    assert sample.cache_read_tokens == 30
    assert sample.cache_create_tokens == 15
    assert sample.cost_usd == 0.042


@pytest.mark.unit
def test_parse_usage_returns_none_without_a_result_envelope() -> None:
    assert ClaudeCodeAdapter().parse_usage("not json at all", "spawn") is None
    assert ClaudeCodeAdapter().parse_usage("", "spawn") is None


@pytest.mark.unit
def test_parse_usage_returns_none_when_envelope_has_no_usage_object() -> None:
    # A killed/verdict-less worker's envelope (if any) carries no `usage` at all —
    # the caller's cue to fall back to `sum_transcript_usage`.
    envelope = json.dumps({"type": "result", "result": "<Choice>pass</Choice>", "session_id": "s1"})
    assert ClaudeCodeAdapter().parse_usage(envelope, "spawn") is None


@pytest.mark.unit
def test_parse_usage_falls_back_to_the_configured_model_when_envelope_omits_it() -> None:
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
    sample = ClaudeCodeAdapter(model="claude-sonnet-5").parse_usage(envelope, "resume")
    assert sample is not None
    assert sample.model == "claude-sonnet-5"
    assert sample.cost_usd is None  # no `total_cost_usd` in this envelope — absent, never fabricated


@pytest.mark.unit
def test_parse_usage_missing_token_fields_default_to_zero() -> None:
    envelope = json.dumps({"type": "result", "result": "ok", "session_id": "s1", "usage": {}})
    sample = ClaudeCodeAdapter().parse_usage(envelope, "spawn")
    assert sample is not None
    counts = (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens)
    assert counts == (0, 0, 0, 0)


@pytest.mark.unit
def test_sum_transcript_usage_sums_multiple_assistant_messages() -> None:
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

    sample = ClaudeCodeAdapter().sum_transcript_usage(lines, "resume")

    assert sample.kind == "resume"
    assert sample.model == "claude-opus-4-8"
    assert sample.input_tokens == 30
    assert sample.output_tokens == 13
    assert sample.cache_read_tokens == 4
    assert sample.cache_create_tokens == 6
    assert sample.cost_usd is None  # a transcript carries no dollar figure — the envelope-less fallback


@pytest.mark.unit
def test_sum_transcript_usage_ignores_non_assistant_and_malformed_lines() -> None:
    lines = [
        "",
        "not json",
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
        json.dumps([1, 2, 3]),  # valid JSON, not a dict record
        json.dumps({"type": "assistant", "message": "not-a-dict"}),
        json.dumps({"type": "assistant", "message": {"usage": "not-a-dict"}}),
    ]

    sample = ClaudeCodeAdapter().sum_transcript_usage(lines, "spawn")

    counts = (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens)
    assert counts == (0, 0, 0, 0)
    assert sample.cost_usd is None


@pytest.mark.unit
def test_sum_transcript_usage_of_empty_transcript_is_zeroed() -> None:
    sample = ClaudeCodeAdapter(model="claude-sonnet-5").sum_transcript_usage([], "judge")

    assert sample.kind == "judge"
    assert sample.model == "claude-sonnet-5"  # nothing to read — falls back to the configured default
    assert sample.input_tokens == 0
    assert sample.cost_usd is None


# --------------------------------------------------------------------------- #
# Injected per-lease stdout redirect (epic #57): spawn / resume_with_message

_FAKE_HARNESS_WITH_USAGE = """#!/usr/bin/env python3
import sys, json
args = sys.argv[1:]
session = resume = prompt = None
output_format = "text"
i = 0
while i < len(args):
    a = args[i]
    if a == "--session-id": session = args[i + 1]; i += 2
    elif a == "--resume": resume = args[i + 1]; i += 2
    elif a == "--output-format": output_format = args[i + 1]; i += 2
    elif a == "--settings": i += 2
    elif a == "--permission-mode": i += 2
    elif a == "--model": i += 2
    elif a in ("-p", "--print"): i += 1
    else: prompt = a; i += 1
sid = resume or session or "auto"
result_text = "<Choice>pass</Choice>" if resume else ""
if output_format != "json":
    print(result_text)
else:
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result_text,
        "session_id": sid,
        "model": "claude-opus-4-8",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        },
        "total_cost_usd": 0.0123,
    }
    print(json.dumps(envelope))
"""


def _fake_binary_with_usage(tmp_path: Path) -> str:
    script = tmp_path / "fake-claude-usage"
    script.write_text(_FAKE_HARNESS_WITH_USAGE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(script)


@pytest.mark.component
def test_spawn_redirects_stdout_to_the_injected_stdout_path(tmp_path: Path) -> None:
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = ClaudeCodeAdapter(binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        stdout_path=str(stdout_path),
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-usage")
    os.waitpid(handle.pid, 0)

    assert stdout_path.exists()
    sample = adapter.parse_usage(stdout_path.read_text(), "spawn")
    assert sample is not None
    assert sample.input_tokens == 100
    assert sample.cost_usd == 0.0123


@pytest.mark.component
def test_spawn_without_a_stdout_path_still_discards_output(tmp_path: Path) -> None:
    # Empty `stdout_path` discards output (DEVNULL) — nothing is left on disk.
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-usage")
    os.waitpid(handle.pid, 0)

    assert list(workdir.glob("*.stdout")) == []


@pytest.mark.component
def test_resume_with_message_redirects_stdout_to_the_injected_path(tmp_path: Path) -> None:
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1-resume.stdout"
    adapter = ClaudeCodeAdapter(binary=binary)

    pid = adapter.resume_with_message(str(workdir), "sess-usage", "deliver the answer", stdout_path=str(stdout_path))
    os.waitpid(pid, 0)

    assert stdout_path.exists()
    sample = adapter.parse_usage(stdout_path.read_text(), "resume")
    assert sample is not None
    assert sample.output_tokens == 50


@pytest.mark.component
def test_resume_with_message_passes_output_format_json_so_cost_is_real(tmp_path: Path) -> None:
    """Regression pin: ``resume_with_message`` must pass ``--output-format json`` so its
    stdout is a JSON envelope and ``parse_usage`` reads the real ``total_cost_usd``."""
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1-resume-cost.stdout"
    adapter = ClaudeCodeAdapter(binary=binary)

    pid = adapter.resume_with_message(str(workdir), "sess-usage", "deliver the answer", stdout_path=str(stdout_path))
    os.waitpid(pid, 0)

    sample = adapter.parse_usage(stdout_path.read_text(), "resume")
    assert sample is not None
    assert sample.cost_usd == 0.0123


@pytest.mark.component
def test_resume_without_output_format_json_yields_no_envelope(tmp_path: Path) -> None:
    """The other side: a resume invocation that omits ``--output-format json`` emits
    plain text, not an envelope, so ``parse_usage`` returns ``None``."""
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = ClaudeCodeAdapter(binary=binary)

    result = subprocess.run(
        [binary, "-p", "--resume", "sess-usage", "deliver the answer"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )

    assert adapter.parse_usage(result.stdout, "resume") is None


# --------------------------------------------------------------------------- #
# Model / effort resolution (issue #144): left-to-right, first entry that resolves wins.


@pytest.mark.unit
def test_resolve_model_maps_the_standard_tiers_with_no_config_at_all() -> None:
    # A zero-config runner resolves the three standard tiers off the adapter's built-ins.
    adapter = ClaudeCodeAdapter(binary="claude")
    assert adapter.resolve_model(["blizzard:frontier"]) == "fable"
    assert adapter.resolve_model(["blizzard:advanced"]) == "opus"
    assert adapter.resolve_model(["blizzard:basic"]) == "sonnet"


@pytest.mark.unit
def test_resolve_model_takes_the_first_entry_that_resolves() -> None:
    adapter = ClaudeCodeAdapter(binary="claude")
    assert adapter.resolve_model(["blizzard:advanced", "blizzard:basic"]) == "opus"


@pytest.mark.unit
def test_resolve_model_skips_a_native_name_belonging_to_another_harness() -> None:
    # A mixed list must fall past a name belonging to another harness rather than hand
    # `claude` a name it would reject, turning a preference into a spawn failure.
    adapter = ClaudeCodeAdapter(binary="claude")
    assert adapter.resolve_model(["gpt-5.3-codex", "blizzard:basic"]) == "sonnet"


@pytest.mark.unit
def test_resolve_model_skips_an_alias_neither_config_nor_builtins_map() -> None:
    adapter = ClaudeCodeAdapter(binary="claude")
    assert adapter.resolve_model(["blizzard:experimental", "blizzard:basic"]) == "sonnet"


@pytest.mark.unit
def test_resolve_model_accepts_a_native_short_name_and_the_claude_family_prefix() -> None:
    adapter = ClaudeCodeAdapter(binary="claude")
    assert adapter.resolve_model(["haiku"]) == "haiku"
    assert adapter.resolve_model(["claude-sonnet-5"]) == "claude-sonnet-5"


@pytest.mark.unit
def test_runner_config_overrides_the_adapters_builtin_tier() -> None:
    adapter = ClaudeCodeAdapter(binary="claude", model_aliases=(("blizzard:basic", "haiku"),))
    assert adapter.resolve_model(["blizzard:basic"]) == "haiku"


@pytest.mark.unit
def test_an_all_unresolvable_list_falls_back_to_the_adapter_default_with_a_note() -> None:
    # Never a spawn failure: an all-unresolvable list is exactly what a mixed-harness
    # fleet produces, and the fallback says which entries it skipped.
    adapter = ClaudeCodeAdapter(binary="claude", model="claude-opus-5")

    with capture_logs() as logs:
        resolved = adapter.resolve_model(["gpt-5.3-codex", "blizzard:experimental"])

    assert resolved == "claude-opus-5"
    note = next(entry for entry in logs if "falling back" in entry["event"])
    assert note["skipped"] == ["gpt-5.3-codex", "blizzard:experimental"]
    assert note["fallback"] == "claude-opus-5"


@pytest.mark.unit
def test_an_empty_preference_list_is_the_adapter_default() -> None:
    # A chunk that expresses no preference (issue #144).
    assert ClaudeCodeAdapter(binary="claude", model="claude-opus-5").resolve_model([]) == "claude-opus-5"


@pytest.mark.unit
@pytest.mark.parametrize("value", ["low", "medium", "high", "max"])
def test_resolve_effort_passes_the_well_known_ordinal_through(value: str) -> None:
    assert ClaudeCodeAdapter(binary="claude").resolve_effort(value) == value


@pytest.mark.unit
def test_resolve_effort_reaches_a_native_tier_outside_the_ordinal_via_config() -> None:
    # `xhigh` is Claude Code's own, outside the well-known four — reachable by alias.
    adapter = ClaudeCodeAdapter(binary="claude", effort_aliases=(("max", "xhigh"),))
    assert adapter.resolve_effort("max") == "xhigh"


@pytest.mark.unit
def test_resolve_effort_of_no_preference_is_none() -> None:
    assert ClaudeCodeAdapter(binary="claude").resolve_effort(None) is None


@pytest.mark.unit
def test_an_unrecognized_effort_logs_once_and_is_ignored() -> None:
    # An unrecognized effort is an authoring mistake — dropped rather than failing a
    # spawn, and noted once rather than on every spawn of a long-lived runner.
    adapter = ClaudeCodeAdapter(binary="claude")

    with capture_logs() as logs:
        assert adapter.resolve_effort("glacial") is None
        assert adapter.resolve_effort("glacial") is None

    assert len([entry for entry in logs if "unrecognized effort" in entry["event"]]) == 1


# The application contract (issue #144): `--model` at mint only (restored on `--resume`);
# `--effort` on every invocation (D5: effort is not sticky).


@pytest.mark.unit
def test_spawn_at_mint_carries_the_resolved_model_and_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture()

    adapter.spawn(envelope, preamble, session_hint="sid", model="sonnet", effort="high")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"


@pytest.mark.unit
def test_spawn_on_a_resume_carries_the_effort_but_never_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture()

    adapter.spawn(envelope, preamble, session_hint="sid", resume_from="prior", model="sonnet", effort="high")

    cmd = captured["cmd"]
    assert "--model" not in cmd  # the harness restores the session's own
    assert cmd[cmd.index("--effort") + 1] == "high"  # not sticky, so reasserted


@pytest.mark.unit
def test_spawn_supplying_neither_behaves_exactly_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    adapter, envelope, preamble = _spawn_fixture()

    adapter.spawn(envelope, preamble, session_hint="sid")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"  # the adapter's own default
    assert "--effort" not in cmd


@pytest.mark.unit
def test_judge_carries_the_effort_but_never_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> object:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    ClaudeCodeAdapter(binary="claude").judge("/ws", "sid", "verdict?", effort="high", model="sonnet")

    cmd = captured["cmd"]
    assert "--model" not in cmd
    assert cmd[cmd.index("--effort") + 1] == "high"


@pytest.mark.unit
def test_resume_with_message_carries_the_effort_but_never_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))

    ClaudeCodeAdapter(binary="claude").resume_with_message("/ws", "sid", "msg", effort="high")

    cmd = captured["cmd"]
    assert "--model" not in cmd
    assert cmd[cmd.index("--effort") + 1] == "high"


# Usage attribution (issue #144): the caller supplies the model resolved for an
# invocation the harness itself reports none for.


@pytest.mark.unit
def test_parse_usage_attributes_to_the_supplied_model_when_the_harness_reports_none() -> None:
    adapter = ClaudeCodeAdapter(binary="claude", model="claude-opus-5")
    output = json.dumps({"result": "x", "usage": {"input_tokens": 5}})

    sample = adapter.parse_usage(output, "spawn", model="sonnet")

    assert sample is not None
    assert sample.model == "sonnet"


@pytest.mark.unit
def test_parse_usage_still_prefers_what_the_harness_itself_reports() -> None:
    adapter = ClaudeCodeAdapter(binary="claude")
    output = json.dumps({"result": "x", "model": "claude-haiku-4-5", "usage": {"input_tokens": 5}})

    sample = adapter.parse_usage(output, "spawn", model="sonnet")

    assert sample is not None
    assert sample.model == "claude-haiku-4-5"


@pytest.mark.unit
def test_parse_usage_without_a_supplied_model_keeps_the_adapter_default() -> None:
    adapter = ClaudeCodeAdapter(binary="claude", model="claude-opus-5")
    output = json.dumps({"result": "x", "usage": {"input_tokens": 5}})

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.model == "claude-opus-5"


@pytest.mark.unit
def test_sum_transcript_usage_attributes_to_the_supplied_model_when_no_line_names_one() -> None:
    adapter = ClaudeCodeAdapter(binary="claude", model="claude-opus-5")
    lines = [json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 3}}})]

    sample = adapter.sum_transcript_usage(lines, "spawn", model="sonnet")

    assert sample.model == "sonnet"
    assert sample.input_tokens == 3


@pytest.mark.unit
def test_the_base_allowlist_carries_no_anthropic_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #144's one deployment requirement, pinned: `ANTHROPIC_MODEL` and its family
    override the restored-session stickiness the mint-only `--model` contract rests on,
    and a leaked one would run every resuming pool member on the wrong model."""
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-haiku-4-5")

    env = AllowlistedEnv.of(()).variables

    assert not [name for name in env if name.startswith("ANTHROPIC_")]


# resume_command (D4, issue #144): the one deliberate exception to mint-only — an
# operator's interactive takeover is neither cache-sensitive nor implicit.


@pytest.mark.unit
def test_resume_command_appends_the_sessions_stamped_model_and_effort() -> None:
    adapter = ClaudeCodeAdapter(binary="claude")

    command = adapter.resume_command("/ws/e1", "sess-a", model="opus", effort="high")

    assert command == "cd /ws/e1 && claude --resume sess-a --model opus --effort high"


@pytest.mark.unit
def test_resume_command_with_no_stamps_renders_todays_bare_command() -> None:
    # A session predating the stamps reads *unknown*: render the bare command rather than
    # guessing at a default and presenting it as what the session ran.
    adapter = ClaudeCodeAdapter(binary="claude")

    assert adapter.resume_command("/ws/e1", "sess-a") == "cd /ws/e1 && claude --resume sess-a"


@pytest.mark.unit
def test_resume_command_appends_only_the_stamp_it_has() -> None:
    adapter = ClaudeCodeAdapter(binary="claude")

    assert (
        adapter.resume_command("/ws/e1", "sess-a", model="opus") == "cd /ws/e1 && claude --resume sess-a --model opus"
    )
    assert (
        adapter.resume_command("/ws/e1", "sess-a", effort="high") == "cd /ws/e1 && claude --resume sess-a --effort high"
    )
