"""Drive a disposable bundle-backed runner against a provisioned env-local hub.

Run from <env>/blizzard with the `winter env <env>` shell exports and BLIZZARD_TMPDIR.
The standing runner is never unpaused or reconfigured. Pass an explicitly disposable,
ready chunk; the route is detached on exit and the runtime is removed.
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
from pathlib import Path


def _request(url: str, body: object | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


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
                selected = _request(f"{hub_url}/api/chunks/{chunk_id}")
                entries = []
                if selected["status"] == "ready":
                    entries = [
                        {
                            "chunk_id": chunk_id,
                            "graph_id": selected["graph_id"],
                            "position": 0,
                            "work_refs": selected.get("work_refs", []),
                            "blocked": selected.get("blocked"),
                        }
                    ]
                self._reply(200, json.dumps({"entries": entries}).encode())
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


def _worker(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "args = sys.argv[1:]\n"
        'if args == ["--version"]:\n'
        '    print("1.18.25")\n'
        "    raise SystemExit(0)\n"
        'if not args or args[0] != "run":\n'
        "    raise SystemExit(2)\n"
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


def _runtime(root: Path, scratch: Path, env: dict[str, str], proxy_url: str) -> Path:
    bundle = scratch / "bundle" / "opencode"
    bundle.mkdir(parents=True)
    (bundle / "opencode.json").write_text('{"permission":{"bash":"deny"}}')
    worker = scratch / "mock-opencode-slow"
    _worker(worker)
    env.update(BZ_RUNNER_DIR=str(root), BZ_RUNNER_TICK_SECONDS="0.75", BLIZZARD_MOCK_HARNESS_FENCE="1")
    env["BZ_HARNESS_BINARY"] = str(Path.cwd().parent / "blizzard-mock/.venv/bin/mock-claude-code")
    subprocess.run(["uv", "run", "blizzard", "runner", "init", str(root)], env=env, check=True, capture_output=True)
    auth = root / "auth.json"
    auth.write_text('{"mock":"present"}')
    config = root / "blizzard-runner.toml"
    text = config.read_text()
    text = text.replace('runner_id = "runner-local"', 'runner_id = "runner-bundle-probe"')
    text = text.replace("[claude_code]\nenabled = true", "[claude_code]\nenabled = false")
    text = text.replace(
        '[opencode]\nenabled = true\nbinary = "opencode"',
        f'[opencode]\nenabled = true\nbinary = "{worker}"\nauth_path = "{auth}"',
    )
    text = text.replace('# config_dir = "~/.config/blizzard/harness"', f'config_dir = "{bundle.parent}"')
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
    hub_chunk = _request(f"{hub_url}/api/chunks/{args.chunk_id}")
    if hub_chunk["status"] != "ready":
        raise RuntimeError(f"chunk {args.chunk_id} must be ready, got {hub_chunk['status']}")
    scratch = Path(scratch_path) / "bundle-runner-probe"
    root = Path.cwd().parent / ".winter" / "bundle-probe"
    if root.exists():
        raise RuntimeError(f"disposable runner runtime already exists: {root}")
    root.mkdir()
    base = f"http://127.0.0.1:{args.port}"
    env = dict(os.environ)
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
            _request(proxy_url + "/api/fleet/routes", {"chunk_id": "__foreign_probe__"})
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        else:
            raise AssertionError("probe guard allowed a foreign chunk claim")
        _runtime(root, scratch, env, proxy_url)
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
                health = _request(base + "/api/health")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        else:
            raise RuntimeError("runner did not become healthy")
        assert health["status"] == "ok"
        bundle_status = subprocess.run(
            ["uv", "run", "blizzard", "runner", "harness", "status", "--dir", str(root)],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert "opencode: source" in bundle_status and "snapshot:" in bundle_status
        for _ in range(120):
            matches = [item for item in _request(base + "/api/leases")["items"] if item["chunk_id"] == args.chunk_id]
            if matches:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"runner did not claim {args.chunk_id}: {log.read_text()[-2500:]}")
        lease = matches[0]
        routed = True
        lease_pid = lease["pid"]
        assert lease["harness_id"] == "opencode"
        ack = _request(base + "/api/heartbeat", {"lease_id": lease["lease_id"]})
        assert ack == {"recorded": True, "lease_id": lease["lease_id"]}
        before = _request(base + "/api/dashboard")["runner"]["last_tick_at"]
        for _ in range(100):
            dashboard = _request(base + "/api/dashboard")
            current = next(
                item for item in _request(base + "/api/leases")["items"] if item["lease_id"] == lease["lease_id"]
            )
            if dashboard["runner"]["last_tick_at"] != before and current["last_heartbeat_at"]:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("heartbeat did not persist across an active lease's tick")
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
                _request(f"{hub_url}/api/chunks/{args.chunk_id}/detach", {})
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
