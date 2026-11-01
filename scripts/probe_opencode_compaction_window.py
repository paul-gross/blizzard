"""Compare real OpenCode compaction with an 8k window, auto, and no window.

Run from the repo root with BLIZZARD_TMPDIR set. All provider traffic is loopback;
the retained scratch directory contains request, worker, and session evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from blizzard.foundation.node_steps import Executor, JudgedBy
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.opencode.adapter import OpenCodeAdapter
from blizzard.runner.harness.opencode.model_limits import FileOpenCodeModelLimitsCatalog
from blizzard.runner.harness.opencode.permissions.permission_resolver import SubprocessOpenCodePermissionResolver
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses
from blizzard.wire.envelope import NodeConfig, NodeEnvelope
from blizzard.wire.graph import SessionMode

_OUTPUT = 1000
_CONTEXT = 32000
_USAGE = (4000, 7900, 8100, 100)


class _Provider(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []
    usage: ClassVar[int] = 100
    used: ClassVar[bool] = False

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(body)
        usage = self.usage if body.get("tools") and not self.used else 100
        if body.get("tools"):
            type(self).used = True
        delta = {"role": "assistant", "content": "probe-finished"}
        finish = "stop"
        chunks = [
            {
                "id": "probe",
                "object": "chat.completion.chunk",
                "model": "stub",
                "choices": [{"index": 0, "delta": delta}],
            },
            {
                "id": "probe",
                "object": "chat.completion.chunk",
                "model": "stub",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": {"prompt_tokens": usage, "completion_tokens": 10, "total_tokens": usage + 10},
            },
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _cell(root: Path, binary: str, window: str | None, executor: ThreadPoolExecutor) -> dict:
    cell = root / (window or "none")
    home, project = cell / "home", cell / "project"
    home.mkdir(parents=True)
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    _Provider.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = {
            "stub": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "stub",
                "options": {"baseURL": f"http://127.0.0.1:{server.server_port}/v1", "apiKey": "x"},
                "models": {
                    "stub": {"name": "stub", "tool_call": True, "limit": {"context": _CONTEXT, "output": _OUTPUT}}
                },
            }
        }
        bundle = cell / "bundle.json"
        bundle.write_text(json.dumps({"plugin": [], "provider": provider}))
        catalog = cell / "catalog.json"
        catalog.write_text(json.dumps(provider))
        os.environ.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".data"),
            XDG_CACHE_HOME=str(home / ".cache"),
        )
        env = AllowlistedEnv.of(("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"))
        process = LinuxProcessProbe()
        children = SubprocessChildProcesses()
        adapter = OpenCodeAdapter(
            binary=binary,
            worker_env=env,
            worker_config_path=str(bundle),
            limits_catalog=FileOpenCodeModelLimitsCatalog(catalog),
            permission_resolver=SubprocessOpenCodePermissionResolver(binary, children=children),
            process=process,
            launcher=ProcessLauncher(process, executor=executor, children=children),
            versions=VersionProbe(children, env.tool()),
        )
        out, err = cell / "turn-0.jsonl", cell / "turn-0.err"
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
            prompt="Report probe-finished.",
            judgement_prompt="assess",
        )
        configured = adapter._compose_compaction(str(project), adapter._config_env(), "stub/stub", window)
        resolver = SubprocessOpenCodePermissionResolver(binary, children=children)
        resolved = resolver.resolve(cwd=str(project), env=env.worker(configured)).merged_config
        (cell / "effective-config.json").write_text(json.dumps(resolved, indent=2))
        (cell / "inline-config.json").write_text(configured["OPENCODE_CONFIG_CONTENT"])
        export_env = {**os.environ, "OPENCODE_CONFIG": str(bundle), "OPENCODE_CONFIG_CONTENT": bundle.read_text()}
        session = None
        turns = []
        for index, usage in enumerate(_USAGE):
            _Provider.usage = usage
            _Provider.used = False
            out, err = cell / f"turn-{index}.jsonl", cell / f"turn-{index}.err"
            preamble = WorkerPreamble(
                environments=[AcquiredEnvironment("probe", str(project))],
                lease_id="lease_probe",
                local_api_url="http://127.0.0.1:9",
                stdout_path=str(out),
                stderr_path=str(err),
                lease_token="token-probe",
            )
            start = len(_Provider.requests)
            handle = adapter.spawn(
                envelope, preamble, None, resume_from=session, model="stub/stub", compaction_window=window
            )
            handle.confirm_durable()
            if session is None:
                session = handle.await_identity(30).session_id
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                pid, status = os.waitpid(handle.pid, os.WNOHANG)
                if pid:
                    break
                time.sleep(0.2)
            else:
                os.killpg(handle.pgid, signal.SIGTERM)
                os.waitpid(handle.pid, 0)
                raise TimeoutError("worker timed out")
            with (
                (cell / f"export-{index}.json").open("w") as exported,
                (cell / f"export-{index}.err").open("w") as errors,
            ):
                subprocess.run(
                    [binary, "export", session],
                    cwd=project,
                    env=export_env,
                    stdout=exported,
                    stderr=errors,
                    timeout=30,
                    check=True,
                )
            export = json.loads((cell / f"export-{index}.json").read_text())
            messages = export["messages"]
            markers = sum(p.get("type") == "compaction" for m in messages for p in m["parts"])
            summaries = sum(m["info"].get("summary") is True for m in messages)
            events = [json.loads(line) for line in out.read_text().splitlines() if line.startswith("{")]
            tokens = [
                e["part"]["tokens"]["input"]
                for e in events
                if e.get("type") == "step_finish" and e["part"].get("tokens")
            ]
            turns.append(
                {
                    "usage_requested": usage,
                    "observed_input": tokens,
                    "exit": os.waitstatus_to_exitcode(status),
                    "compactions": markers,
                    "summaries": summaries,
                    "requests": len(_Provider.requests) - start,
                }
            )
            (cell / "requests.json").write_text(json.dumps(_Provider.requests, indent=2))
        caps = [r.get("max_tokens", r.get("max_completion_tokens")) for r in _Provider.requests]
        result = {
            "turns": turns,
            "output_caps": caps,
            "session": session,
        }
        expected = [0, 0, 1, 1] if window not in ("auto", None) else [0, 0, 0, 0]
        crossing = json.loads((cell / "export-2.json").read_text())["messages"]
        sequence = []
        for message in crossing:
            for part in message["parts"]:
                if part.get("type") == "step-finish" and part.get("tokens", {}).get("input") == 8100:
                    sequence.append("crossed")
                if part.get("type") == "compaction":
                    sequence.append("compaction")
            if message["info"].get("summary") is True:
                sequence.append("summary")
        result["crossing_sequence"] = sequence
        result["ok"] = (
            all(turn["exit"] == 0 and turn["usage_requested"] in turn["observed_input"] for turn in turns)
            and [turn["compactions"] for turn in turns] == expected
            and [turn["summaries"] for turn in turns] == expected
            and sequence == (["crossed", "compaction", "summary"] if window not in ("auto", None) else ["crossed"])
            and len(caps) >= len(_USAGE)
            and all(cap == _OUTPUT for cap in caps)
        )
        return result
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--window", required=True, choices=["8k"])
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args()
    scratch = os.environ.get("BLIZZARD_TMPDIR")
    if not scratch:
        raise RuntimeError("BLIZZARD_TMPDIR must name this lease's scratch directory")
    root = args.evidence_dir.resolve()
    if not root.is_relative_to(Path(scratch).resolve()) or root == Path(scratch).resolve():
        raise ValueError("evidence directory must be inside BLIZZARD_TMPDIR")
    if not Path(args.binary).is_absolute():
        raise ValueError("binary must be an absolute path")
    root.mkdir(parents=True, exist_ok=False)
    version = subprocess.run([args.binary, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    (root / "version.txt").write_text(version + "\n")
    numbers = tuple(int(part) for part in version.split(".")[:3])
    if not ((1, 18, 25) <= numbers < (2, 0, 0)):
        raise RuntimeError(f"OpenCode version outside admitted range: {version}")
    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        for window in (args.window, "auto", None):
            try:
                results[window or "none"] = _cell(root, args.binary, window, executor)
            except Exception as exc:
                results[window or "none"] = {"ok": False, "error": str(exc)}
    report = json.dumps({"version": version, "evidence": str(root), "cells": results}, indent=2)
    (root / "result.json").write_text(report + "\n")
    print(report)
    raise SystemExit(0 if all(cell["ok"] for cell in results.values()) else 1)


if __name__ == "__main__":
    main()
