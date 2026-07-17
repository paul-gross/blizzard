"""The Claude Code adapter — verdict parsing (unit) and a real subprocess (component).

``parse_verdict`` is exercised in isolation over the harness-native JSON envelope and
its failure modes (``bzh:`` unit tier). The component test drives the adapter against
a real fake-harness binary that mimics ``mock-claude-code``'s CLI surface — spawn
launches a real process (its pid + start time stamped) in the acquired
workdir, and the judgement resume's output is parsed into a choice. The real
``mock-claude-code`` façade is bound in the e2e (``blizzard:e2e``).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
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
    assert "--model claude-opus-4-8" in (workdir / "argv.txt").read_text()  # pinned Opus, not the ambient default


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
