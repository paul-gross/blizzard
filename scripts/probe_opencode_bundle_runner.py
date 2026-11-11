"""Drive a disposable bundle-backed runner against a provisioned env-local hub.

Run from <env>/blizzard with the `winter env <env>` shell exports and BLIZZARD_TMPDIR.
The standing runner is never unpaused or reconfigured. Pass an explicitly disposable,
ready chunk; the route is detached on exit and the runtime is removed.

The runner gets a scratch XDG_DATA_HOME so the operator's own OpenCode store never decides its availability: one already
migrated to OpenCode 2 withdraws the OpenCode 1 binding the probe claims through. While the lease
is held a changed bundle is published from this process, moving `current`; every worker launch records the
OPENCODE_CONFIG / OPENCODE_CONFIG_DIR it was given, and each must still name the snapshot the runner started with.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TypedDict, TypeVar
from unittest.mock import patch

from blizzard.runner.harness.internal.bundle_publisher import published_snapshot
from blizzard.runner.harness.wiring import first_bindings, publish_harness_bundle
from blizzard.runner.host_config import HostConfig


class Chunk(TypedDict):
    status: str
    graph_id: str
    work_refs: list[object]
    blocked: object
    project_id: str | None
    project_slug: str | None
    project_name: str | None


class Health(TypedDict):
    status: str


class Runner(TypedDict):
    state: str
    runner_id: str


class Runners(TypedDict):
    items: list[Runner]


class Lease(TypedDict):
    chunk_id: str
    lease_id: str
    pid: int
    harness_id: str
    last_heartbeat_at: str | None


class Leases(TypedDict):
    items: list[Lease]


class DashboardRunner(TypedDict):
    last_tick_at: str | None


class Dashboard(TypedDict):
    runner: DashboardRunner


T = TypeVar("T")


def _request[T](url: str, response_type: type[T], body: object | None = None) -> T:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def _selected_queue(chunk_id: str, selected: Chunk) -> dict[str, list[dict[str, object]]]:
    entries: list[dict[str, object]] = []
    if selected["status"] == "ready":
        entries.append(
            {
                "chunk_id": chunk_id,
                "graph_id": selected["graph_id"],
                "position": 0,
                "work_refs": selected.get("work_refs", []),
                "blocked": selected.get("blocked"),
                "project_id": selected["project_id"],
                "project_slug": selected["project_slug"],
                "project_name": selected["project_name"],
            }
        )
    return {"entries": entries}


def _guarded_hub(hub_url: str, chunk_id: str) -> http.server.ThreadingHTTPServer:
    """Only expose the selected chunk at FILL's read and claim doors."""

    class Guard(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _forward(self) -> None:
            path = self.path.split("?", 1)[0]
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if path == "/api/fleet/queue/peek" and self.command in {"GET", "POST"}:
                selected = _request(f"{hub_url}/api/chunks/{chunk_id}", Chunk)
                self._reply(200, json.dumps(_selected_queue(chunk_id, selected)).encode())
                return
            if path == "/api/fleet/routes" and self.command == "POST" and json.loads(body).get("chunk_id") != chunk_id:
                self._reply(409, b'{"detail":"probe refuses a foreign chunk claim"}')
                return
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() not in {"host", "connection", "content-length", "accept-encoding"}
            }
            upstream = urllib.request.Request(
                hub_url + self.path,
                data=body if self.command in {"POST", "PUT", "PATCH"} else None,
                method=self.command,
                headers=headers,
            )
            try:
                with urllib.request.urlopen(upstream, timeout=15) as response:
                    self._reply(
                        response.status, response.read(), response.headers.get("Content-Type", "application/json")
                    )
            except urllib.error.HTTPError as exc:
                self._reply(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json"))

        def do_GET(self) -> None:
            self._forward()

        def do_POST(self) -> None:
            self._forward()

        def do_PUT(self) -> None:
            self._forward()

        def do_DELETE(self) -> None:
            self._forward()

    return http.server.ThreadingHTTPServer(("127.0.0.1", 0), Guard)


def _worker(path: Path, record: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys, time\n"
        "args = sys.argv[1:]\n"
        'if args == ["--version"]:\n'
        '    print("1.18.25")\n'
        "    raise SystemExit(0)\n"
        'if not args or args[0] != "run":\n'
        "    raise SystemExit(2)\n"
        f"with open({str(record)!r}, 'a') as record:\n"
        "    names = ('OPENCODE_CONFIG', 'OPENCODE_CONFIG_DIR')\n"
        "    record.write(json.dumps({k: os.environ.get(k) for k in names}) + '\\n')\n"
        'session = args[args.index("--session") + 1] if "--session" in args else "ses_bundle_probe"\n'
        'print(json.dumps({"type": "step_start", "sessionID": session, "part": '
        '{"id": "prt_start", "sessionID": session, "messageID": "msg_probe", "type": "step-start"}}), flush=True)\n'
        "time.sleep(8)\n"
        'print(json.dumps({"type": "text", "sessionID": session, "part": '
        '{"id": "prt_text", "sessionID": session, "messageID": "msg_probe", '
        '"type": "text", "text": "<Choice>pass</Choice>"}}), flush=True)\n'
        'print(json.dumps({"type": "step_finish", "sessionID": session, "part": '
        '{"id": "prt_finish", "sessionID": session, "messageID": "msg_probe", '
        '"type": "step-finish", "reason": "stop", "cost": 0.0, '
        '"tokens": {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}}}}), flush=True)\n'
    )
    path.chmod(0o755)


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _launches(scratch: Path) -> list[dict[str, str | None]]:
    record = scratch / "worker-launches.jsonl"
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []


def _runtime(root: Path, scratch: Path, env: dict[str, str], proxy_url: str) -> Path:
    bundle = scratch / "bundle" / "opencode"
    bundle.mkdir(parents=True)
    (bundle / "opencode.json").write_text('{"permission":{"bash":"deny"}}')
    worker = scratch / "mock-opencode-slow"
    _worker(worker, scratch / "worker-launches.jsonl")
    # OpenCode's store lives beside auth.json under XDG_DATA_HOME; HOME stays, since `winter` and `uv` read theirs.
    data_home = scratch / "xdg-data"
    data_home.mkdir()
    # mise keeps its installed tools under XDG_DATA_HOME too; pin it where it already is so `winter` still finds them.
    env.setdefault("MISE_DATA_DIR", os.environ.get("MISE_DATA_DIR") or str(_data_home() / "mise"))
    env["XDG_DATA_HOME"] = str(data_home)
    env.update(BZ_RUNNER_DIR=str(root), BZ_RUNNER_TICK_SECONDS="0.75", BLIZZARD_MOCK_HARNESS_FENCE="1")
    env["BZ_HARNESS_BINARY"] = str(Path.cwd().parent / "blizzard-mock/.venv/bin/mock-claude-code")
    # The disposable runner joins the hub as a runner of its own: init adds it under its own token, never the
    # standing runner's from the shell exports.
    for name in [name for name in env if name.startswith("BZ_HUB_TOKEN") or name == "BZ_HOST_TOKEN"]:
        del env[name]
    with patch.dict(os.environ, env, clear=True):
        scaffold = HostConfig.scaffold(root, hub_url=env["BZ_HUB_URL"])
    (runner,) = scaffold.runners
    scaffold = replace(scaffold, runners=(replace(runner, name=f"bundle-probe-{uuid.uuid4().hex}"),))
    config = root / "blizzard-host.toml"
    config.write_text(scaffold.to_toml())
    subprocess.run(
        ["uv", "run", "blizzard", "runner", "init", str(root), "--hub", env["BZ_HUB_URL"]],
        env=env,
        check=True,
        capture_output=True,
    )
    auth = root / "auth.json"
    auth.write_text('{"mock":"present"}')
    text = config.read_text()
    text = text.replace("[runner.claude_code]\nenabled = true", "[runner.claude_code]\nenabled = false")
    text = text.replace(
        '[runner.opencode]\nenabled = true\nbinary = "opencode"',
        f'[runner.opencode]\nenabled = true\nbinary = "{worker}"\nauth_path = "{auth}"',
    )
    text = text.replace(
        '[runner.harness]\nautonomy = "dangerous"',
        f'[runner.harness]\nautonomy = "dangerous"\nconfig_dir = "{bundle.parent}"',
    )
    assert text.count("[runner.worker]\nenv_passthrough = []") == 1
    text = text.replace(
        "[runner.worker]\nenv_passthrough = []", '[runner.worker]\nenv_passthrough = ["XDG_DATA_HOME", "MISE_DATA_DIR"]'
    )
    direct_hub = f'hub_url = "{env["BZ_HUB_URL"]}"'
    assert text.count(direct_hub) == 1
    text = text.replace(direct_hub, f'hub_url = "{proxy_url}"')
    config.write_text(text)
    env["BZ_HUB_URL"] = proxy_url
    return bundle.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-id", required=True, help="An explicitly disposable ready chunk in the env-local hub")
    parser.add_argument("--port", type=int, default=4693)
    args = parser.parse_args()
    scratch_path = os.environ.get("BLIZZARD_TMPDIR")
    hub_url = os.environ.get("BZ_HUB_URL")
    if not scratch_path or not hub_url or not os.environ.get("BZ_WORKSPACE_ROOT"):
        raise RuntimeError("BLIZZARD_TMPDIR and `winter env <env>` exports are required")
    hub_chunk = _request(f"{hub_url}/api/chunks/{args.chunk_id}", Chunk)
    if hub_chunk["status"] != "ready":
        raise RuntimeError(f"chunk {args.chunk_id} must be ready, got {hub_chunk['status']}")
    scratch = Path(scratch_path) / "bundle-runner-probe"
    root = Path.cwd().parent / ".winter" / "bundle-probe"
    if scratch.exists():
        raise RuntimeError(f"disposable probe scratch already exists: {scratch}")
    if root.exists():
        raise RuntimeError(f"disposable runner runtime already exists: {root}")
    root.mkdir()
    base = f"http://127.0.0.1:{args.port}"
    # The runner presents the token `runner init` adds it with at the env-local hub, never one from this shell.
    env = {name: value for name, value in os.environ.items() if not name.startswith("BZ_HUB_TOKEN")}
    proxy = _guarded_hub(hub_url, args.chunk_id)
    proxy_url = f"http://127.0.0.1:{proxy.server_port}"
    proxy_thread = threading.Thread(target=proxy.serve_forever, name="bundle-probe-hub", daemon=True)
    proxy_thread.start()
    process: subprocess.Popen[bytes] | None = None
    log = root / "host.log"
    routed = False
    lease_pid: int | None = None
    try:
        try:
            _request(proxy_url + "/api/fleet/routes", dict[str, object], {"chunk_id": "__foreign_probe__"})
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        else:
            raise AssertionError("probe guard allowed a foreign chunk claim")
        bundle_dir = _runtime(root, scratch, env, proxy_url)
        with log.open("wb") as output:
            process = subprocess.Popen(
                ["uv", "run", "blizzard", "runner", "host", "--dir", str(root), "--port", str(args.port)],
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError(f"runner exited before readiness: {log.read_text()[-2500:]}")
            try:
                health = _request(base + "/api/health", Health)
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        else:
            raise RuntimeError("runner did not become healthy")
        assert health["status"] == "ok"
        (served,) = [
            item for item in _request(base + "/api/host/runners", Runners)["items"] if item["state"] == "serving"
        ]
        runner = f"{base}/runners/{served['runner_id']}"
        bundle_status = subprocess.run(
            ["uv", "run", "blizzard", "runner", "harness", "status", "--dir", str(root)],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert "opencode: source" in bundle_status and "snapshot:" in bundle_status
        started_on = Path(next(line for line in bundle_status.splitlines() if line.startswith("snapshot:"))[9:].strip())
        for _ in range(120):
            matches = [
                item for item in _request(runner + "/api/leases", Leases)["items"] if item["chunk_id"] == args.chunk_id
            ]
            if matches:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"runner did not claim {args.chunk_id}: {log.read_text()[-2500:]}")
        lease = matches[0]
        routed = True
        lease_pid = lease["pid"]
        assert lease["harness_id"] == "opencode"
        ack = _request(runner + "/api/heartbeat", dict[str, object], {"lease_id": lease["lease_id"]})
        assert ack == {"recorded": True, "lease_id": lease["lease_id"]}
        before = _request(runner + "/api/dashboard", Dashboard)["runner"]["last_tick_at"]
        for _ in range(100):
            dashboard = _request(runner + "/api/dashboard", Dashboard)
            current = next(
                item
                for item in _request(runner + "/api/leases", Leases)["items"]
                if item["lease_id"] == lease["lease_id"]
            )
            if dashboard["runner"]["last_tick_at"] != before and current["last_heartbeat_at"]:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("heartbeat did not persist across an active lease's tick")
        launches = _launches(scratch)
        assert launches, "the worker recorded no launch"
        (bundle_dir / "opencode" / "opencode.json").write_text('{"permission":{"bash":"allow"}}')
        moved = publish_harness_bundle(bundle_dir, root, selection=first_bindings())
        assert moved.path.resolve() != started_on.resolve(), "publishing a changed bundle did not move current"
        assert published_snapshot(root) == moved.path.resolve()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and len(_launches(scratch)) == len(launches):
            time.sleep(0.25)
        launches = _launches(scratch)
        expected = {
            "OPENCODE_CONFIG": str(started_on / "opencode" / "opencode.json"),
            "OPENCODE_CONFIG_DIR": str(started_on / "opencode"),
        }
        assert all(launch == expected for launch in launches), (expected, launches)
        status = subprocess.run(
            ["uv", "run", "blizzard", "runner", "status", "--runner-url", base],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert lease["lease_id"] in status
        print(
            json.dumps(
                {
                    "health": health["status"],
                    "snapshot_published": True,
                    "lease_id": lease["lease_id"],
                    "chunk_id": args.chunk_id,
                    "harness_id": lease["harness_id"],
                    "heartbeat_recorded": True,
                    "last_heartbeat_at": current["last_heartbeat_at"],
                    "tick_after_heartbeat": dashboard["runner"]["last_tick_at"],
                    "cli_confirmed_lease": True,
                    "started_on_snapshot": str(started_on),
                    "current_moved_to": str(moved.path),
                    "worker_launches_checked": len(launches),
                },
                indent=2,
            )
        )
        subprocess.run(
            ["uv", "run", "blizzard", "runner", "pause", "--runner-url", base, "--by", "bundle-probe"],
            env=env,
            check=True,
            capture_output=True,
        )
    finally:
        if lease_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(lease_pid, signal.SIGTERM)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=20)
        if routed:
            try:
                _request(f"{hub_url}/api/chunks/{args.chunk_id}/detach", dict[str, object], {})
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
        proxy.shutdown()
        proxy.server_close()
        proxy_thread.join(timeout=5)
        shutil.rmtree(root)
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
