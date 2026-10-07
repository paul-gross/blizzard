"""Prove a headless worker's permission request never stalls, on both harnesses across every autonomy value.

Run with BLIZZARD_TMPDIR set, from the blizzard repo root. Launches go through the production adapters under a
disposable HOME and XDG tree and a scratch project. OpenCode talks to a deterministic local OpenAI-compatible stub,
so its tool call is guaranteed and no credential is needed; Claude Code uses the real login and a cheap model
(``--skip-claude`` omits it). Prints a sanitized per-cell verdict and exits non-zero if any cell fails.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from blizzard.foundation.node_steps import Executor, JudgedBy
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.opencode.adapter import OpenCodeAdapter
from blizzard.runner.harness.opencode.permissions.permission_resolver import SubprocessOpenCodePermissionResolver
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.wire.envelope import NodeConfig, NodeEnvelope
from blizzard.wire.graph import SessionMode

_TIMEOUT_SECONDS = 120
_FINAL_TEXT = "probe-finished"
#: One tool call per ask source: the tool the stub requests, and the arguments it passes.
_ASK_CELLS: dict[str, tuple[str, dict[str, str]]] = {
    "bundle": ("bash", {"command": "echo bundle-ask", "description": "probe"}),
    "user": ("webfetch", {"url": "http://127.0.0.1:9/", "format": "text"}),
    "project": ("edit", {"filePath": "probe.txt", "oldString": "a", "newString": "b"}),
    "agent": ("write", {"filePath": "probe-agent.txt", "content": "x"}),
    "default": ("read", {"filePath": ".env"}),
}


class _Stub(BaseHTTPRequestHandler):
    """An OpenAI-compatible chat endpoint: the first turn requests the cell's tool, the next replies."""

    tool: tuple[str, dict[str, str]] = ("bash", {})

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        has_tool_result = any(message.get("role") == "tool" for message in body.get("messages", []))
        if has_tool_result:
            delta: dict[str, object] = {"role": "assistant", "content": _FINAL_TEXT}
            finish = "stop"
        else:
            name, arguments = self.tool
            call = {"index": 0, "id": "call_probe", "type": "function"}
            call["function"] = {"name": name, "arguments": json.dumps(arguments)}
            delta = {"role": "assistant", "tool_calls": [call]}
            finish = "tool_calls"
        chunks = [
            {"id": "c", "object": "chat.completion.chunk", "model": "stub", "choices": [{"index": 0, "delta": delta}]},
            {
                "id": "c",
                "object": "chat.completion.chunk",
                "model": "stub",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _opencode_project(root: Path, port: int) -> tuple[Path, Path, Path]:
    home = root / "home"
    project = root / "project"
    bundle = root / "bundle.json"
    (home / ".config" / "opencode").mkdir(parents=True)
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    (project / ".env").write_text("SECRET=1\n")
    (project / "probe.txt").write_text("a\n")
    provider = {
        "stub": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "stub",
            "options": {"baseURL": f"http://127.0.0.1:{port}/v1", "apiKey": "x"},
            "models": {"stub": {"name": "stub", "tool_call": True, "limit": {"context": 8000, "output": 1000}}},
        }
    }
    bundle.write_text(
        json.dumps({"permission": {"question": "deny", "bash": "ask"}, "plugin": [], "provider": provider})
    )
    (home / ".config" / "opencode" / "opencode.json").write_text(json.dumps({"permission": {"webfetch": "ask"}}))
    (project / "opencode.json").write_text(
        json.dumps({"permission": {"edit": "ask"}, "agent": {"build": {"permission": {"write": "ask"}}}})
    )
    return home, project, bundle


def _wait(pid: int) -> str:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            return f"exit {os.waitstatus_to_exitcode(status)}"
        time.sleep(0.2)
    os.killpg(pid, signal.SIGTERM)
    os.waitpid(pid, 0)
    return "timeout"


def _spawn(
    adapter: OpenCodeAdapter | ClaudeCodeAdapter, project: Path, out: Path, err: Path, model: str | None, prompt: str
) -> str:
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment("probe", str(project))],
        lease_id="lease_probe",
        local_api_url="http://127.0.0.1:9",
        stdout_path=str(out),
        stderr_path=str(err),
        lease_token="token-probe",
    )
    node = NodeConfig(
        node_id="nd_probe",
        node_name="build",
        executor=Executor.RUNNER,
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
    )
    envelope = NodeEnvelope(
        chunk_id="ch_probe",
        graph_id="gr_probe",
        epoch=1,
        node=node,
        prompt=prompt,
        judgement_prompt="assess",
    )
    handle = adapter.spawn(
        envelope, preamble, None if isinstance(adapter, OpenCodeAdapter) else str(uuid.uuid4()), model=model
    )
    handle.confirm_durable()
    return _wait(handle.pid)


def _opencode_cell(autonomy: Autonomy, source: str, root: Path, binary: str, executor: ThreadPoolExecutor) -> str:
    tool, arguments = _ASK_CELLS[source]
    _Stub.tool = (tool, arguments)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cell = root / f"oc-{autonomy.value}-{source}"
    cell.mkdir()
    try:
        home, project, bundle = _opencode_project(cell, server.server_port)
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".data"))
        process = LinuxProcessProbe()
        adapter = OpenCodeAdapter(
            binary=binary,
            worker_env=AllowlistedEnv.of(("XDG_CONFIG_HOME", "XDG_DATA_HOME")),
            worker_config_path=str(bundle),
            autonomy=autonomy,
            permission_resolver=SubprocessOpenCodePermissionResolver(binary),
            process=process,
            launcher=ProcessLauncher(process, executor=executor),
        )
        out, err = cell / "out.jsonl", cell / "err.log"
        outcome = _spawn(adapter, project, out, err, "stub/stub", "Use the one tool you are asked to, then report.")
        lines = out.read_text().splitlines() if out.exists() else []
        records = [json.loads(line) for line in lines if line.startswith("{")]
        tool_states = [
            r["part"]["state"] for r in records if r.get("type") == "tool_use" and r["part"].get("tool") == tool
        ]
        errors = " ".join(str(state.get("error", "")) for state in tool_states)
        finished = any(r.get("type") == "text" and _FINAL_TEXT in json.dumps(r) for r in records)
        stderr = err.read_text() if err.exists() else ""
        if outcome != "exit 0":
            return f"FAIL {outcome}"
        completed = any(state.get("status") == "completed" for state in tool_states)
        if autonomy is Autonomy.Normal:
            # A bare `deny` removes the tool, a pattern `deny` returns a rule error: either way the call is
            # refused as a tool result and the loop reaches its final reply.
            ok = finished and not completed and "auto-rejecting" not in stderr and "rejected" not in errors
            return (
                "ok refused as a tool error, loop continued"
                if ok
                else f"FAIL finished={finished} completed={completed}"
            )
        ok = finished and "prevents" not in errors and "rejected" not in errors
        return "ok resolved without a prompt" if ok else f"FAIL finished={finished} error={errors[:80]!r}"
    finally:
        server.shutdown()


def _claude_cell(autonomy: Autonomy, root: Path, binary: str, model: str, executor: ThreadPoolExecutor) -> str:
    cell = root / f"cc-{autonomy.value}"
    project = cell / "project"
    project.mkdir(parents=True)
    settings = cell / "settings.json"
    settings.write_text(json.dumps({"permissions": {"ask": ["Bash(echo blizzard-probe-ask*)"]}}))
    process = LinuxProcessProbe()
    adapter = ClaudeCodeAdapter(
        binary,
        settings_path=str(settings),
        autonomy=autonomy,
        worker_env=AllowlistedEnv.of(("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")),
        process=process,
        launcher=ProcessLauncher(process, executor=executor),
    )
    out, err = cell / "out.jsonl", cell / "err.log"
    outcome = _spawn(
        adapter, project, out, err, model, "Run exactly this with the Bash tool, then report: echo blizzard-probe-ask"
    )
    records = [json.loads(line) for line in out.read_text().splitlines() if line.startswith("{")]
    result = next((r for r in reversed(records) if r.get("type") == "result"), {})
    denied = result.get("permission_denials") or []
    if outcome != "exit 0" or not result:
        return f"FAIL {outcome}"
    return "ok refused as a tool error, turn completed" if denied else "FAIL no permission_denials entry"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", default="opencode")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--claude-model", default="haiku")
    parser.add_argument("--skip-claude", action="store_true")
    args = parser.parse_args()
    temp = os.environ.get("BLIZZARD_TMPDIR")
    if not temp:
        raise RuntimeError("BLIZZARD_TMPDIR must name this lease's scratch directory")
    verdicts: dict[str, str] = {}
    login_env = {key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME") if key in os.environ}
    with tempfile.TemporaryDirectory(dir=temp) as work, ThreadPoolExecutor(max_workers=2) as executor:
        root = Path(work)
        for autonomy in Autonomy:
            for source in _ASK_CELLS:
                verdicts[f"opencode/{autonomy.value}/{source}"] = _opencode_cell(
                    autonomy, source, root, args.opencode, executor
                )
            if not args.skip_claude:
                # The OpenCode cells point HOME at a disposable tree; Claude Code needs the real login.
                os.environ.update(login_env)
                verdicts[f"claude/{autonomy.value}"] = _claude_cell(
                    autonomy, root, args.claude, args.claude_model, executor
                )
    print(json.dumps(verdicts, indent=2))
    raise SystemExit(0 if all(v.startswith("ok") for v in verdicts.values()) else 1)


if __name__ == "__main__":
    main()
