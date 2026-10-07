"""Exercise the production OpenCode bundle binding with a real, authenticated CLI.

Run with BLIZZARD_TMPDIR set, from the blizzard repo root. The login is copied into
lease scratch; never print the credential or include it in the report. This probe
uses a harmless hook-command recorder instead of a live runner lease endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from blizzard.foundation.node_steps import Executor, JudgedBy
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.bundle import HarnessBundleError
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal.bundle_publisher import published_snapshot
from blizzard.runner.harness.opencode.adapter import OpenCodeAdapter
from blizzard.runner.harness.opencode.bundle import check_ambient_plugins
from blizzard.runner.harness.opencode.declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.wiring import publish_harness_bundle
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.wire.envelope import NodeConfig, NodeEnvelope
from blizzard.wire.graph import SessionMode


def _write_plugin(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'import { appendFileSync } from "node:fs";\n'
        f'export const Marker = async () => ({{ "tool.execute.after": async (input) => '
        f"appendFileSync(process.env.PROBE_EVENT_FILE, `{label} ${{input.tool}}\\n`) }});\n"
    )


def _login(source: Path, destination: Path) -> None:
    credential = json.loads(source.read_text())
    expires = credential.get("openai", {}).get("expires")
    if not isinstance(expires, int) or expires / 1000 - time.time() < 3600:
        raise RuntimeError("an unexpired OpenAI login with at least one hour remaining is required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _setup(root: Path, login: Path, model: str) -> tuple[Path, Path, Path, Path]:
    home = root / "home"
    user = home / ".config" / "opencode"
    project = root / "project"
    source = root / "bundle" / "opencode"
    runtime = root / "runtime"
    for directory in (user, project, source / "plugins", source / "prompts", runtime):
        directory.mkdir(parents=True, exist_ok=True)
    _login(login, home / ".local" / "share" / "opencode" / "auth.json")
    (user / "opencode.json").write_text('{"username":"bundle-probe"}')
    _write_plugin(user / "plugins" / "global.js", "global")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "note.txt").write_text("hello from bundle probe\n")
    (project / "opencode.json").write_text(json.dumps({"model": model}))
    _write_plugin(project / ".opencode" / "plugins" / "local.js", "local")
    _write_plugin(source / "plugins" / "marker.js", "operator")
    (source / "prompts" / "explore.txt").write_text("Explore only, probe companion loaded.")
    (source / "opencode.json").write_text(
        json.dumps({"permission": {"bash": "deny"}, "agent": {"explore": {"prompt": "{file:./prompts/explore.txt}"}}})
    )
    OPENCODE_DECLARATION.scaffold_runtime(
        OpenCodeSection(worker_config_path=str(runtime / "opencode-worker-config.json")), runtime
    )
    snapshot = publish_harness_bundle(source.parent, runtime)
    previous = published_snapshot(runtime)
    assert previous == snapshot.path.resolve()
    native = source / "opencode.json"
    original = native.read_text()
    companion = json.loads(original)["agent"]
    try:
        native.write_text(json.dumps({"permission": {"question": "allow"}, "agent": companion}))
        try:
            publish_harness_bundle(source.parent, runtime)
        except HarnessBundleError as exc:
            assert exc.path == native
        else:
            raise AssertionError("runner-owned question collision was accepted")
        assert published_snapshot(runtime) == previous
        runner_plugins = json.loads((runtime / "opencode-worker-config.json").read_text())["plugin"]
        native.write_text(json.dumps({"plugin": runner_plugins, "agent": companion}))
        try:
            publish_harness_bundle(source.parent, runtime)
        except HarnessBundleError as exc:
            assert exc.path == native
        else:
            raise AssertionError("runner plugin collision was accepted")
        assert published_snapshot(runtime) == previous
        native.write_text(json.dumps({"plugin": [(source / "plugins" / "marker.js").as_uri()], "agent": companion}))
        try:
            publish_harness_bundle(source.parent, runtime)
        except HarnessBundleError as exc:
            assert str(native) in str(exc) and str(source / "plugins" / "marker.js") in str(exc)
        else:
            raise AssertionError("operator JSON/directory duplicate was accepted")
        assert published_snapshot(runtime) == previous
    finally:
        native.write_text(original)
    effective = snapshot.path / "opencode"
    duplicate = project / ".opencode" / "plugins" / "MARKER.ts"
    duplicate.write_text("export const Duplicate = async () => ({});")
    try:
        try:
            check_ambient_plugins(effective, project, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")})
        except HarnessBundleError as exc:
            assert str(effective / "plugins" / "marker.js") in str(exc) and str(duplicate) in str(exc)
        else:
            raise AssertionError("ambient duplicate was accepted")
    finally:
        duplicate.unlink()
    for config in (project / "opencode.json", user / "opencode.json"):
        original_config = config.read_text()
        try:
            document = json.loads(original_config)
            document["plugin"] = ["file:///elsewhere/marker.ts"]
            config.write_text(json.dumps(document))
            try:
                check_ambient_plugins(effective, project, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")})
            except HarnessBundleError as exc:
                assert str(effective / "plugins" / "marker.js") in str(exc) and str(config) in str(exc)
            else:
                raise AssertionError(f"ambient JSON duplicate was accepted: {config}")
        finally:
            config.write_text(original_config)
    return home, project, runtime, effective


def _events(path: Path) -> tuple[list[str], list[dict]]:
    records = [json.loads(line) for line in path.read_text().splitlines() if line.startswith("{")]
    errors = [entry for entry in records if entry.get("type") == "error"]
    assert not errors, errors
    tools = [
        entry["part"]["tool"]
        for entry in records
        if entry.get("type") == "tool_use" and entry["part"].get("state", {}).get("status") == "completed"
    ]
    return tools, records


def _wait(pid: int, timeout: float = 100) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            assert os.waitstatus_to_exitcode(status) == 0, (pid, status)
            return
        time.sleep(0.1)
    os.killpg(pid, signal.SIGTERM)
    os.waitpid(pid, 0)
    raise TimeoutError(f"OpenCode pid {pid} did not exit in {timeout}s")


def _run(
    adapter: OpenCodeAdapter, project: Path, root: Path, name: str, prompt: str, session: str | None
) -> tuple[str, list[str]]:
    out = root / f"{name}.jsonl"
    preamble = WorkerPreamble(
        environments=[AcquiredEnvironment("probe", str(project))],
        lease_id="lease_probe",
        local_api_url="http://127.0.0.1:9",
        stdout_path=str(out),
        stderr_path=str(root / f"{name}.err"),
        lease_token="token-probe",
    )
    if name in {"fresh", "resume"}:
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
        handle = adapter.spawn(envelope, preamble, None, resume_from=session)
    elif name == "judge":
        assert session
        handle = adapter.judge(str(project), session, prompt, str(out), preamble=preamble, chunk_id="ch_probe")
    else:
        assert session
        handle = adapter.resume_with_message(
            str(project), session, prompt, str(out), preamble=preamble, chunk_id="ch_probe"
        )
    handle.confirm_durable()
    minted = handle.await_identity(10).session_id if name == "fresh" else session
    _wait(handle.pid)
    tools, records = _events(out)
    if name == "fresh":
        assert minted == records[0]["sessionID"]
    return minted or "", tools


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", type=Path, default=Path.home() / ".local/share/opencode/auth.json")
    parser.add_argument("--model", default="openai/gpt-5.6-luna")
    parser.add_argument("--binary", default="opencode")
    args = parser.parse_args()
    temp = os.environ.get("BLIZZARD_TMPDIR")
    if not temp:
        raise RuntimeError("BLIZZARD_TMPDIR must name this lease's scratch directory")
    version = subprocess.run([args.binary, "--version"], text=True, capture_output=True, check=True).stdout.strip()
    with tempfile.TemporaryDirectory(dir=temp) as work:
        root = Path(work)
        home, project, _runtime, effective = _setup(root, args.login, args.model)
        stub = root / "bin" / "blizzard"
        stub.parent.mkdir()
        stub.write_text('#!/bin/sh\n/usr/bin/printf "runner heartbeat\\n" >> "$PROBE_EVENT_FILE"\n')
        stub.chmod(0o755)
        events = root / "events.txt"
        os.environ.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local/share"),
            PROBE_EVENT_FILE=str(events),
            PATH=f"{stub.parent}:{os.environ['PATH']}",
        )
        worker_env = AllowlistedEnv.of(("XDG_CONFIG_HOME", "XDG_DATA_HOME", "PROBE_EVENT_FILE"))
        process = LinuxProcessProbe()
        with ThreadPoolExecutor(max_workers=2) as executor:
            adapter = OpenCodeAdapter(
                binary=args.binary,
                worker_env=worker_env,
                worker_config_path=str(effective / "opencode.json"),
                effective_config_dir=str(effective),
                process=process,
                launcher=ProcessLauncher(process, executor=executor),
            )
            preamble = WorkerPreamble([AcquiredEnvironment("probe", str(project))], "lease_probe", "http://127.0.0.1:9")
            resolved = subprocess.run(
                [args.binary, "debug", "config"],
                cwd=project,
                env=adapter.identity_env(preamble, "ch_probe", "ses_probe"),
                text=True,
                capture_output=True,
                timeout=90,
                check=True,
            )
            document = json.loads(resolved.stdout)
            assert document["permission"]["bash"] == document["permission"]["question"] == "deny"
            assert document["username"] == "bundle-probe" and document["model"] == args.model
            assert document["agent"]["explore"]["prompt"] == "Explore only, probe companion loaded."
            origins = [Path(item["spec"]).name for item in document["plugin_origins"]]
            assert len(origins) == len(set(origins)) == 4, origins
            assert set(origins) == {"global.js", "local.js", "marker.js", "blizzard-heartbeat.ts"}, origins
            counts: dict[str, list[str]] = {}
            session, counts["fresh"] = _run(
                adapter,
                project,
                root,
                "fresh",
                "Read note.txt with the read tool exactly once. Then report the contents.",
                None,
            )
            prompts = {
                "resume": "Read note.txt once more and say resumed.",
                "nudge": "Read note.txt again and say nudged.",
                "judge": "Read note.txt, then say pass.",
                "deny_bash": "Use the bash tool to run pwd. Do not use other tools.",
                "deny_question": "Use the question tool to ask whether to proceed. Do not use other tools.",
                "child": "Use the task tool to ask a subagent to read note.txt exactly once.",
            }
            for name, prompt in prompts.items():
                _, counts[name] = _run(adapter, project, root, name, prompt, session)
        for name in ("fresh", "resume", "nudge", "judge"):
            assert counts[name] == ["read"], (name, counts[name])
        assert counts["deny_bash"] == counts["deny_question"] == []
        assert counts["child"] == ["task"], counts["child"]
        lines = events.read_text().splitlines()
        plugin_tools = {
            name: [line.removeprefix(f"{name} ") for line in lines if line.startswith(f"{name} ")]
            for name in ("global", "local", "operator")
        }
        assert plugin_tools["global"] == plugin_tools["local"] == plugin_tools["operator"], plugin_tools
        assert len(plugin_tools["global"]) >= sum(map(len, counts.values())), (plugin_tools, counts)
        assert lines.count("runner heartbeat") == len(plugin_tools["global"]), lines
        report = {
            "version": version,
            "source_paths": {
                "operator": str(root / "bundle" / "opencode" / "opencode.json"),
                "user": str(home / ".config" / "opencode" / "opencode.json"),
                "project": str(project / "opencode.json"),
            },
            "effective_config": str(effective / "opencode.json"),
            "effective_sha256": hashlib.sha256((effective / "opencode.json").read_bytes()).hexdigest(),
            "settings": ["username", "model", "permission.bash", "permission.question", "agent.explore.prompt"],
            "plugin_origins": origins,
            "tool_calls_by_invocation": counts,
            "plugin_calls_each": len(plugin_tools["global"]),
            "runner_heartbeat_calls": lines.count("runner heartbeat"),
            "native_collisions_rejected": True,
            "ambient_duplicate_rejected": True,
        }
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
