"""The Claude Code adapter — verdict parsing (unit) and a real subprocess (component).

``parse_verdict`` is exercised over the harness-native JSON envelope and its failure
modes; the component test drives spawn/resume against a real fake-harness binary. Also
covers usage parsing, the per-lease stdout redirect, and node-entry resume.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from concurrent.futures import Executor
from pathlib import Path

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.support.claude_code_adapter import SENTINEL_UNLISTED_VAR as _SENTINEL_UNLISTED_VAR
from tests.support.claude_code_adapter import adapter as _adapter
from tests.support.runner_fakes import make_envelope


def test_judge_child_env_excludes_the_hub_token_and_an_unlisted_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN", "super-secret-token")
    monkeypatch.setenv(_SENTINEL_UNLISTED_VAR, "should-not-leak")
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=str(dump_script))

    handle = adapter.judge(str(workdir), "sess-1", "assess", str(workdir / "judge-output.json"))
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert "BZ_HUB_TOKEN" not in dumped
    assert _SENTINEL_UNLISTED_VAR not in dumped


def test_judge_injects_the_lease_identity_when_given_a_preamble(tmp_path: Path, spawn_executor: Executor) -> None:
    # `--resume` inherits none of the spawn env, so a judge given a preamble must carry
    # the same per-lease identity a resume does.
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=str(dump_script))
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_42",
        local_api_url="http://127.0.0.1:8431",
        lease_token="fresh-judge-token",
    )

    handle = adapter.judge(
        str(workdir), "sess-9", "assess", str(workdir / "judge-output.json"), preamble=preamble, chunk_id="ch_9"
    )
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert dumped["BLIZZARD_LEASE_ID"] == "lease_42"
    assert dumped["BLIZZARD_RUNNER_URL"] == "http://127.0.0.1:8431"
    assert dumped["BLIZZARD_LEASE_TOKEN"] == "fresh-judge-token"  # the re-minted plaintext rides the judge
    assert dumped["BLIZZARD_CHUNK_ID"] == "ch_9"


def test_judge_child_env_carries_the_elicitation_marker_when_given_a_preamble(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=str(dump_script))
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_42",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.judge(
        str(workdir), "sess-9", "assess", str(workdir / "judge-output.json"), preamble=preamble, chunk_id="ch_9"
    )
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert dumped["BLIZZARD_ELICITATION"] == "1"


def test_resume_with_message_child_env_excludes_the_hub_token_and_an_unlisted_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN", "super-secret-token")
    monkeypatch.setenv(_SENTINEL_UNLISTED_VAR, "should-not-leak")
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=str(dump_script))

    resumed = adapter.resume_with_message(str(workdir), "sess-1", "deliver")
    resumed.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(resumed.pid, 0)

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert "BZ_HUB_TOKEN" not in dumped
    assert _SENTINEL_UNLISTED_VAR not in dumped


def test_resume_with_message_injects_the_lease_identity_when_given_a_preamble(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    # A resumed worker needs the same per-lease identity a fresh spawn gets, since
    # `--resume` inherits none of the spawn env — the resume child env must carry it too.
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=str(dump_script))
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_42",
        local_api_url="http://127.0.0.1:8431",
        lease_token="fresh-resume-token",
    )

    resumed = adapter.resume_with_message(str(workdir), "sess-9", "continue", preamble=preamble, chunk_id="ch_9")
    resumed.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(resumed.pid, 0)

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert dumped["BLIZZARD_LEASE_ID"] == "lease_42"
    assert dumped["BLIZZARD_RUNNER_URL"] == "http://127.0.0.1:8431"
    assert dumped["BLIZZARD_LEASE_TOKEN"] == "fresh-resume-token"  # the re-minted plaintext rides the resume
    assert dumped["BLIZZARD_CHUNK_ID"] == "ch_9"


def test_resume_with_message_child_env_excludes_the_elicitation_marker(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    dump_script = tmp_path / "dump-env"
    dump_script.write_text(_ENV_DUMP_HARNESS)
    dump_script.chmod(dump_script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=str(dump_script))
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_42",
        local_api_url="http://127.0.0.1:8431",
    )

    resumed = adapter.resume_with_message(str(workdir), "sess-9", "continue", preamble=preamble, chunk_id="ch_9")
    resumed.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(resumed.pid, 0)

    dumped = json.loads((workdir / "env-dump.json").read_text())
    assert "BLIZZARD_ELICITATION" not in dumped


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


def test_spawn_launches_real_process_in_workdir(tmp_path: Path, spawn_executor: Executor) -> None:
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123").await_identity(0)

    assert handle.session_id == "sess-123"  # Claude honors the pre-assigned id
    assert handle.pid > 0
    assert handle.process_start_time  # stamped from /proc for pid-reuse-proof liveness
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)  # let the fire-and-forget child finish
    assert (workdir / "spawned-here.txt").read_text() == (envelope.prompt or "")  # ran in the acquired workdir
    assert "--permission-mode bypassPermissions" in (workdir / "argv.txt").read_text()  # the autonomy default
    assert "--model claude-opus-5" in (workdir / "argv.txt").read_text()  # pinned Opus, not the ambient default


_VERSION_HANGS_HARNESS = """#!/usr/bin/env python3
import sys, json, time
args = sys.argv[1:]
if args == ["--version"]:
    time.sleep(30)
    sys.exit(0)
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
    open("spawned-here.txt", "w").write(prompt or "")
    result = ""
else:
    result = "Assessed. <Choice>pass</Choice>"
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result, "session_id": sid}))
"""


def test_a_hung_version_probe_reads_none_and_the_spawn_right_after_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    """``observe_version``, read immediately BEFORE the spawn, hangs past its own bound and
    reads back ``None`` instead of blocking — the spawn right after it, on the same binary,
    is entirely unaffected."""
    # `observe_version` is shared verbatim with OpenCode — the bound lives on
    # `version_probe`, not on this adapter module.
    monkeypatch.setattr("blizzard.runner.harness.internal.version_probe.VERSION_PROBE_TIMEOUT_SECONDS", 0.2)
    script = tmp_path / "hung-version-claude"
    script.write_text(_VERSION_HANGS_HARNESS)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=str(script))
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    assert adapter.observe_version() is None  # bounded — never waits out the hang

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    assert handle.pid > 0
    assert (workdir / "spawned-here.txt").read_text() == (envelope.prompt or "")


def test_spawn_pins_a_configured_model(tmp_path: Path, spawn_executor: Executor) -> None:
    # The worker model is pinned so a spawn never inherits the operator's ambient
    # ``claude`` default; the constructor argument overrides the Opus default.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary, model="claude-sonnet-5")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    assert "--model claude-sonnet-5" in (workdir / "argv.txt").read_text()


def test_spawn_passes_the_permission_mode_flag_when_configured(tmp_path: Path, spawn_executor: Executor) -> None:
    # A headless worker has no one to approve tool use; the configured permission mode is
    # what lets it edit/commit in its sandboxed worktree.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary, permission_mode="bypassPermissions")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    assert "--permission-mode bypassPermissions" in (workdir / "argv.txt").read_text()


def test_judge_resume_output_parses_to_choice(tmp_path: Path, spawn_executor: Executor) -> None:
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary)
    output_path = str(workdir / "judge-output.json")

    handle = adapter.judge(str(workdir), "sess-123", "Assess the build. Reply <Choice>name</Choice>.", output_path)
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    output = Path(output_path).read_text()
    assert adapter.parse_verdict(output) == "pass"


def test_judge_passes_the_permission_mode_flag_when_configured(tmp_path: Path, spawn_executor: Executor) -> None:
    # ``--permission-mode`` is per-invocation, not session-sticky, so ``judge`` must
    # reassert it exactly as ``spawn``/``resume_with_message`` do.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary, permission_mode="bypassPermissions")

    handle = adapter.judge(
        str(workdir), "sess-123", "Assess. Reply <Choice>name</Choice>.", str(workdir / "judge-output.json")
    )
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    assert "--permission-mode bypassPermissions" in (workdir / "argv.txt").read_text()


def test_resume_with_message_carries_the_worker_settings_hooks(tmp_path: Path, spawn_executor: Executor) -> None:
    # ``--resume`` does not inherit the spawn's ``--settings``, so a resumed session
    # would lose its heartbeat/session-end hooks unless this re-attaches the file.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    settings = tmp_path / "worker-settings.json"
    adapter = _adapter(spawn_executor, binary=binary, settings_path=str(settings))

    resumed = adapter.resume_with_message(str(workdir), "sess-123", "continue where you left off")
    resumed.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(resumed.pid, 0)

    assert f"--settings {settings}" in (workdir / "argv.txt").read_text()


def test_judge_prefix_matches_resume_with_messages_settings_and_effort(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    # ``judge``'s resume must reuse the same flags, in the same order, as the session's
    # real turns, or it recreates the cache instead of reading it.
    binary = _fake_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    settings = tmp_path / "worker-settings.json"
    adapter = _adapter(
        spawn_executor=spawn_executor, binary=binary, settings_path=str(settings), permission_mode="bypassPermissions"
    )

    resumed = adapter.resume_with_message(
        str(workdir), "sess-123", "continue", model="sonnet", effort="high", compaction_window="150k"
    )
    resumed.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(resumed.pid, 0)
    resumed_prefix, _, resumed_arg = (workdir / "argv.txt").read_text().rpartition(" ")

    judge_handle = adapter.judge(
        str(workdir),
        "sess-123",
        "assess",
        str(workdir / "judge-output.json"),
        model="sonnet",
        effort="high",
        compaction_window="150k",
    )
    judge_handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(judge_handle.pid, 0)
    judge_prefix, _, judge_arg = (workdir / "argv.txt").read_text().rpartition(" ")

    assert judge_prefix == resumed_prefix
    assert resumed_arg == "continue"
    assert judge_arg == "assess"
    assert f"--settings {settings}" in judge_prefix
    assert "--effort high" in judge_prefix
    assert "--model sonnet" in judge_prefix
    assert "--autocompact 150k" in judge_prefix
    assert "--permission-mode bypassPermissions" in judge_prefix


def test_spawn_runs_at_workspace_root_and_prepends_prefix(tmp_path: Path, spawn_executor: Executor) -> None:
    # The worker's cwd is the winter workspace root, not the env subdir, and the
    # runner-composed preamble is prepended to the node envelope prompt.
    binary = _fake_binary(tmp_path)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_workdir = workspace_root / "r1"
    env_workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="r1", workdir=str(env_workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        workspace_root=str(workspace_root),
        prompt_prefix="PREAMBLE-TABLE",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    # Ran at the workspace root — the marker file the fake writes lands there, not the env dir.
    assert (workspace_root / "spawned-here.txt").exists()
    assert not (env_workdir / "spawned-here.txt").exists()
    # The composed prompt is the prefix followed by the envelope prompt.
    assert (workspace_root / "spawned-here.txt").read_text() == f"PREAMBLE-TABLE\n\n{envelope.prompt}"


def test_spawn_falls_back_to_env_workdir_without_a_workspace_root(tmp_path: Path, spawn_executor: Executor) -> None:
    # An empty workspace_root keeps the legacy cwd (the first env's workdir).
    binary = _fake_binary(tmp_path)
    env_workdir = tmp_path / "r1"
    env_workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="r1", workdir=str(env_workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-123")
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    # No prefix and no workspace root: cwd is the env workdir, prompt is the envelope prompt alone.
    assert (env_workdir / "spawned-here.txt").read_text() == (envelope.prompt or "")


# --------------------------------------------------------------------------- #
# Usage extraction (epic #57, #58): parse_usage, sum_transcript_usage

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


def test_spawn_redirects_stdout_to_the_injected_stdout_path(tmp_path: Path, spawn_executor: Executor) -> None:
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        stdout_path=str(stdout_path),
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-usage")
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    assert stdout_path.exists()
    sample = adapter.parse_usage(stdout_path.read_text(), "spawn")
    assert sample is not None
    assert sample.input_tokens == 100
    assert sample.cost_usd == 0.0123


def test_spawn_without_a_stdout_path_still_discards_output(tmp_path: Path, spawn_executor: Executor) -> None:
    # Empty `stdout_path` discards output (DEVNULL) — nothing is left on disk.
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
    )

    handle = adapter.spawn(envelope, preamble, session_hint="sess-usage")
    handle.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(handle.pid, 0)

    assert list(workdir.glob("*.stdout")) == []


def test_resume_with_message_redirects_stdout_to_the_injected_path(tmp_path: Path, spawn_executor: Executor) -> None:
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1-resume.stdout"
    adapter = _adapter(spawn_executor, binary=binary)

    resumed = adapter.resume_with_message(
        str(workdir), "sess-usage", "deliver the answer", stdout_path=str(stdout_path)
    )
    resumed.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(resumed.pid, 0)

    assert stdout_path.exists()
    sample = adapter.parse_usage(stdout_path.read_text(), "resume")
    assert sample is not None
    assert sample.output_tokens == 50


def test_resume_with_message_passes_output_format_json_so_cost_is_real(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    """Regression pin: ``resume_with_message`` must pass ``--output-format json`` so its
    stdout is a JSON envelope and ``parse_usage`` reads the real ``total_cost_usd``."""
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1-resume-cost.stdout"
    adapter = _adapter(spawn_executor, binary=binary)

    resumed = adapter.resume_with_message(
        str(workdir), "sess-usage", "deliver the answer", stdout_path=str(stdout_path)
    )
    resumed.confirm_durable()  # stands in for the caller's own confirm_durable()
    os.waitpid(resumed.pid, 0)

    sample = adapter.parse_usage(stdout_path.read_text(), "resume")
    assert sample is not None
    assert sample.cost_usd == 0.0123


def test_resume_without_output_format_json_yields_no_envelope(tmp_path: Path, spawn_executor: Executor) -> None:
    """The other side: a resume invocation that omits ``--output-format json`` emits
    plain text, not an envelope, so ``parse_usage`` returns ``None``."""
    binary = _fake_binary_with_usage(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary)

    result = subprocess.run(
        [binary, "-p", "--resume", "sess-usage", "deliver the answer"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )

    assert adapter.parse_usage(result.stdout, "resume") is None


# --------------------------------------------------------------------------- #
# Model / effort resolution: left-to-right, first entry that resolves wins.
