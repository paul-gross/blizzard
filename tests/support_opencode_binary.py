"""A local fake OpenCode CLI, and the levers that steer it into one named misbehaviour.

Not a provider client and not a mock harness: a hermetic stand-in for the real binary's surface,
so the diagnostic's process, allowlist, disposable-git, parser, report, and evidence bindings run
with no network access.  The mock fleet owns the first-class version of this.
"""

from __future__ import annotations

import stat
from pathlib import Path

from blizzard.runner.harness.internal.opencode_probe import PINNED_OPENCODE_VERSION

MODEL = "openai/gpt-5.6-luna"
VARIANT = "max"
PROVIDER_SECRET = "provider-secret-sentinel"

_FAKE_OPENCODE = r"""#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PERMISSION_REQUEST_ONLY = False
PERMISSION_PROSE_ONLY = False
PERMISSION_DUPLICATE = False
PERMISSION_OS_ERROR = False
PERMISSION_NONZERO = False
IGNORE_CONFIG = False
DROP_CONFIG_SHELL = False
DROP_CONFIG_COMPACTION = False
PROVIDER_REFUSAL = False
CONFIGURATION_PROSE_ONLY = False
CONFIGURATION_OS_ERROR = False
STATIC_REPLAY = False
FRESH_NONZERO = False
PROCESS_CONTROL_NO_LIVE_STATE = False
TAKEOVER_WRONG_DIRECTORY = False
TAKEOVER_WRONG_SESSION = False
TAKEOVER_NON_SSE = False
TAKEOVER_EXIT_EARLY = False
TAKEOVER_IDLE_SSE = False
TAKEOVER_IMMEDIATE_EOF = False
TAKEOVER_STREAM_FAILURE = False
TAKEOVER_EVENT_GATED = False
SECURITY_COMMAND_EXECUTES = False
MUTATE_AUTH = False
READ_AUTH = False
AUTH_READ_MARKER = None
COMPACTION_NO_CHANGE = False
VERSION_TOUCH_PATH = None
STATE_PATH = os.path.join(os.environ.get("XDG_STATE_HOME", "."), "fake-opencode-state.json")
TAKEOVER_EVENT = threading.Event()


def record_takeover_input():
    value = sys.stdin.readline().strip()
    if not value:
        return
    state = {"phase": "takeover_continued", "takeover_prompt": value}
    try:
        with open(STATE_PATH) as fh:
            state = json.load(fh)
    except FileNotFoundError:
        pass
    state["phase"] = "takeover_continued"
    state["takeover_prompt"] = value
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh)

args = sys.argv[1:]
if args == ["--version"]:
    if READ_AUTH:
        auth_path = os.path.join(os.environ.get("XDG_DATA_HOME", "."), "opencode", "auth.json")
        try:
            with open(auth_path, "rb") as fh:
                fh.read(1)
        except FileNotFoundError:
            status = "missing"
        except OSError:
            status = "unreadable"
        else:
            status = "present"
        if AUTH_READ_MARKER:
            try:
                with open(AUTH_READ_MARKER, "w") as fh:
                    fh.write(status)
            except OSError:
                pass
    if MUTATE_AUTH:
        auth_path = os.path.join(os.environ.get("XDG_DATA_HOME", "."), "opencode", "auth.json")
        try:
            with open(auth_path, "w") as fh:
                fh.write("mutated by fake")
        except OSError:
            pass
    if VERSION_TOUCH_PATH:
        with open(os.path.join(os.getcwd(), VERSION_TOUCH_PATH), "w") as fh:
            fh.write("touched")
    print("opencode 1.18.25")
    raise SystemExit(0)
if args[:2] == ["debug", "agent"] and "--tool" in args:
    if IGNORE_CONFIG:
        print("The runner-owned configuration was ignored.", file=sys.stderr)
    elif PERMISSION_REQUEST_ONLY:
        print("The tool call requested permission but was not denied.", file=sys.stderr)
    elif PERMISSION_PROSE_ONLY:
        print("The model described a denied tool call without executing it.", file=sys.stderr)
    else:
        message = "The user has specified a rule which prevents you from using this specific tool call."
        print(message, file=sys.stderr)
        if PERMISSION_DUPLICATE:
            print(message, file=sys.stderr)
    raise SystemExit(1)
if args[:3] == ["debug", "config", "--pure"]:
    if IGNORE_CONFIG:
        print(json.dumps({"model": "competing/ignored-model"}))
    else:
        try:
            effective = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
        except json.JSONDecodeError:
            effective = {}
        if DROP_CONFIG_SHELL:
            effective.pop("shell", None)
        if DROP_CONFIG_COMPACTION:
            effective.pop("compaction", None)
        print(json.dumps(effective))
    raise SystemExit(0)
print("Authorization: Bearer provider-secret-sentinel", file=sys.stderr)
if args and args[0] == "--session":
    time.sleep(30)
    raise SystemExit(0)
if args and args[0] == "serve":
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/session/") and self.path.endswith("/children"):
                body = json.dumps({"children": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/session/"):
                sid = self.path.split("/")[2]
                body = json.dumps({
                    "id": "ses_wrong" if TAKEOVER_WRONG_SESSION else sid,
                    "directory": "/wrong-directory" if TAKEOVER_WRONG_DIRECTORY else os.getcwd(),
                    "title": "proof",
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.split("?", 1)[0] in {"/event", "/global/event"}:
                if TAKEOVER_EVENT_GATED:
                    TAKEOVER_EVENT.wait(30)
                if TAKEOVER_STREAM_FAILURE:
                    body = b"upstream failure"
                    status = 503
                    content_type = "text/event-stream"
                else:
                    body = b"{}" if TAKEOVER_NON_SSE else b": upstream\n\n"
                    status = 200
                    content_type = "application/json" if TAKEOVER_NON_SSE else "text/event-stream"
                if TAKEOVER_IMMEDIATE_EOF:
                    body = b""
                    status = 200
                    content_type = "text/event-stream"
                elif TAKEOVER_IDLE_SSE:
                    body = b""
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("X-Upstream-Stream", "preserved")
                if not TAKEOVER_IDLE_SSE:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)
                    self.wfile.flush()
                if TAKEOVER_IDLE_SSE:
                    time.sleep(30)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/session":
                TAKEOVER_EVENT.set()
            if self.path.endswith("/summarize") and not COMPACTION_NO_CHANGE:
                state = {"phase": "done", "compaction_generation": 0}
                try:
                    with open(STATE_PATH) as fh:
                        state = json.load(fh)
                except FileNotFoundError:
                    pass
                state["phase"] = "summarized"
                state["compaction_generation"] = int(state.get("compaction_generation", 0)) + 1
                with open(STATE_PATH, "w") as fh:
                    json.dump(state, fh)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"true")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"opencode server listening on http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
if args[:2] == ["session", "children"]:
    print(json.dumps({"children": []}))
    raise SystemExit(0)
if args and args[0] == "export":
    sid = args[1]
    state = {"phase": "done"}
    try:
        with open(STATE_PATH) as fh:
            state = json.load(fh)
    except FileNotFoundError:
        pass
    phase = state.get("phase")
    running = phase in {"running", "process_control"} and not STATIC_REPLAY
    compaction_generation = int(state.get("compaction_generation", 0))
    command = "sleep 30" if phase == "process_control" else "sleep 5"
    tool_state = {"status": "running", "input": {"command": command}, "title": "Long-running command"}
    if not running:
        tool_state = {"status": "completed", "input": {"command": "sleep 5"},
                      "output": "slept", "title": "Long-running command", "metadata": {}}
    export = {
        "info": {"id": sid, "version": "1.18.25",
                 "directory": "/wrong-directory" if TAKEOVER_WRONG_DIRECTORY else os.getcwd(), "title": "proof"},
        "messages": [
            {"info": {"id": "msg_user", "sessionID": sid, "role": "user"}, "parts": [
                {"id": "prt_user", "sessionID": sid, "messageID": "msg_user", "type": "text", "text": "proof"}
            ]},
            {"info": {"id": "msg_assistant", "sessionID": sid, "role": "assistant",
                       "providerID": "openai", "modelID": "gpt-5.6-luna", "variant": "max",
                       "tokens": {"input": 2, "output": 3, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                       "cost": 0.01}, "parts": [
                {"id": "prt_start", "sessionID": sid, "messageID": "msg_assistant", "type": "step-start"},
                {"id": "prt_tool", "sessionID": sid, "messageID": "msg_assistant", "type": "tool", "callID": "call_sleep",
                 "tool": "bash", "state": tool_state}
            ]}
        ]
    }
    if not running:
        export["messages"] = [export["messages"][1]]
        export["messages"].append({"info": {"id": "msg_compaction_user", "sessionID": sid, "role": "user"},
                                   "parts": [{"id": f"prt_compaction_{compaction_generation}", "sessionID": sid,
                                               "messageID": "msg_compaction_user", "type": "compaction"}]})
        export["messages"].append({"info": {"id": "msg_after_compaction", "sessionID": sid, "role": "assistant",
                                              "providerID": "openai", "modelID": "gpt-5.6-luna", "variant": "max"},
                                   "parts": [{"id": "prt_after_compaction", "sessionID": sid,
                                              "messageID": "msg_after_compaction", "type": "text",
                                              "text": "After compaction."}]})
        export["messages"][0]["parts"].extend([
            {"id": "prt_text", "sessionID": sid, "messageID": "msg_assistant", "type": "text",
             "text": "Done. <Choice>pass</Choice>"},
            {"id": "prt_finish", "sessionID": sid, "messageID": "msg_assistant", "type": "step-finish",
             "reason": "stop", "cost": 0.01,
             "tokens": {"input": 2, "output": 3, "reasoning": 0, "cache": {"read": 0, "write": 0}}}
        ])
    takeover_prompt = state.get("takeover_prompt")
    if isinstance(takeover_prompt, str):
        export["messages"].append({"info": {"id": "msg_takeover_user", "sessionID": sid, "role": "user"},
                                    "parts": [{"id": "prt_takeover_user", "sessionID": sid,
                                               "messageID": "msg_takeover_user", "type": "text",
                                               "text": takeover_prompt}]})
    print(json.dumps(export))
    raise SystemExit(0)

if args and args[0] == "attach":
    import urllib.request
    attach_url = args[1]
    session = args[args.index("--session") + 1]
    headers = {"X-OpenCode-Directory": args[args.index("--dir") + 1]}
    with urllib.request.urlopen(
        urllib.request.Request(f"{attach_url}/session/{session}", headers=headers), timeout=10
    ) as response:
        response.read()
    with urllib.request.urlopen(
        urllib.request.Request(f"{attach_url}/global/event", headers=headers), timeout=10
    ) as response:
        if response.headers.get("X-Upstream-Stream") != "preserved":
            raise SystemExit(8)
        if TAKEOVER_IDLE_SSE:
            if response.headers.get("Content-Type") != "text/event-stream":
                raise SystemExit(8)
        elif response.read() != b": upstream\n\n" and not TAKEOVER_IMMEDIATE_EOF and not TAKEOVER_STREAM_FAILURE:
            raise SystemExit(8)
    if TAKEOVER_EXIT_EARLY:
        raise SystemExit(0)
    if TAKEOVER_IDLE_SSE:
        threading.Thread(target=record_takeover_input, daemon=True).start()
        time.sleep(30)
    else:
        record_takeover_input()
    print("interactive attach connected", flush=True)
    time.sleep(30)
    raise SystemExit(0)

if "process-control turn" in (args[-1] if args else ""):
    if not PROCESS_CONTROL_NO_LIVE_STATE:
        with open(STATE_PATH, "w") as fh:
            json.dump({"phase": "process_control"}, fh)
    print(json.dumps({"type": "step_start", "sessionID": "ses_process_control",
                      "part": {"id": "prt_process_control_start", "sessionID": "ses_process_control",
                               "messageID": "msg_process_control", "type": "step-start"}}), flush=True)
    time.sleep(30)
if "permission" in (args[-1] if args else ""):
    sid = "ses_permission"
    if PERMISSION_PROSE_ONLY:
        events = [{"type": "step_start", "sessionID": sid,
                   "part": {"id": "prt_permission_start", "sessionID": sid,
                             "messageID": "msg_permission", "type": "step-start"}},
                  {"type": "text", "sessionID": sid,
                   "part": {"id": "prt_permission_text", "sessionID": sid,
                             "messageID": "msg_permission", "type": "text",
                             "text": "The rule prevents you from using this specific tool call."}}]
    else:
        events = []
        if PERMISSION_REQUEST_ONLY:
            events.append({"type": "tool_use", "sessionID": sid,
                           "part": {"id": "prt_permission", "sessionID": sid,
                                     "messageID": "msg_permission", "type": "tool", "callID": "call_permission",
                                     "tool": "bash", "state": {"status": "error",
                                                                  "input": {"command": "printf permission-probe"},
                                                                  "error": "unrelated tool failure"}}})
        else:
            events.append({"type": "tool_use", "sessionID": sid,
                           "part": {"id": "prt_permission", "sessionID": sid,
                                     "messageID": "msg_permission", "type": "tool", "callID": "call_permission",
                                     "tool": "bash", "state": {"status": "error", "input": {"command": "printf permission-probe"},
                                                                  "error": "The user has specified a rule which prevents you from using this specific tool call."}}})
        if PERMISSION_DUPLICATE:
            events.append(events[-1] | {"part": events[-1]["part"] | {"id": "prt_permission_duplicate"}})
        if PERMISSION_OS_ERROR:
            events[-1]["part"]["state"]["error"] = "permission denied"
    print("\n".join(json.dumps(event) for event in events))
    if PERMISSION_NONZERO:
        raise SystemExit(9)
    raise SystemExit(0)

if "configuration turn" in (args[-1] if args else ""):
    if CONFIGURATION_PROSE_ONLY:
        print("The configured permission rule prevented this specific tool call.")
        raise SystemExit(0)
    denied = False
    if not IGNORE_CONFIG:
        try:
            def read_config(path):
                try:
                    with open(path) as fh:
                        return json.load(fh)
                except (OSError, json.JSONDecodeError):
                    return None

            config = read_config(os.environ.get("OPENCODE_CONFIG", ""))
            if config is None and os.environ.get("OPENCODE_DISABLE_PROJECT_CONFIG") != "1":
                config = read_config(os.path.join(os.getcwd(), "opencode.json"))
            if config is None:
                config = read_config(
                    os.path.join(os.environ.get("XDG_CONFIG_HOME", ""), "opencode", "opencode.json")
                )
            if config is None:
                config = {}
            if os.environ.get("OPENCODE_CONFIG_CONTENT"):
                config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
            bash_permission = config["permission"]["bash"]
            denied = bash_permission == "deny" or (
                isinstance(bash_permission, dict)
                and bash_permission["printf config-isolation-probe"] == "deny"
            )
        except KeyError:
            pass
    status = "error" if denied else "completed"
    state = {"status": status, "input": {"command": "printf config-isolation-probe"}}
    if denied:
        state["error"] = "permission denied" if CONFIGURATION_OS_ERROR else "The configured permission rule prevented this specific tool call."
    else:
        state["output"] = "config-isolation-probe"
    event = {"type": "tool_use", "sessionID": "ses_config", "part": {
        "id": "prt_config", "sessionID": "ses_config", "messageID": "msg_config",
        "type": "tool", "callID": "call_config", "tool": "bash", "state": state}}
    print(json.dumps(event))
    raise SystemExit(0)

if "security proof" in (args[-1] if args else ""):
    sid = "ses_security"
    command = args[-1].split("`", 2)[1]
    if SECURITY_COMMAND_EXECUTES:
        subprocess.run(command, shell=True, check=False)
        state = {"status": "completed", "input": {"command": command}, "output": "executed"}
    else:
        state = {"status": "error", "input": {"command": command},
                 "error": "The user has specified a rule which prevents you from using this specific tool call."}
    event = {"type": "tool_use", "sessionID": sid, "part": {
        "id": "prt_security", "sessionID": sid, "messageID": "msg_security",
        "type": "tool", "callID": "call_security", "tool": "bash",
        "state": state}}
    print(json.dumps(event))
    raise SystemExit(0)

sid = "ses_fake"
if PROVIDER_REFUSAL:
    print(json.dumps({"type": "error", "sessionID": sid,
                      "error": {"name": "APIError",
                                "data": {"message": "The usage limit has been reached", "statusCode": 429}}}), flush=True)
    time.sleep(30)
    raise SystemExit(0)
if "--session" in args:
    sid = args[args.index("--session") + 1]
else:
    with open("compatibility-proof.txt", "w") as fh:
        fh.write("ok\n")
    subprocess.run(["git", "add", "compatibility-proof.txt"], check=True)
    subprocess.run(["git", "-c", "user.email=fake@blizzard.local", "-c", "user.name=fake",
                    "commit", "-q", "-m", "compatibility: fake proof"], check=True)
if TAKEOVER_WRONG_SESSION and "--dir" in args:
    sid = "ses_wrong"

if "sleep 5" in (args[-1] if args else ""):
    with open(STATE_PATH, "w") as fh:
        json.dump({"phase": "running"}, fh)
    print(json.dumps({"type": "step_start", "sessionID": sid,
                      "part": {"id": "prt_start", "sessionID": sid, "messageID": "msg_assistant", "type": "step-start"}}), flush=True)
    time.sleep(1.0)
    with open(STATE_PATH, "w") as fh:
        json.dump({"phase": "done"}, fh)

events = [
    {"type": "step_start", "sessionID": sid,
     "part": {"id": "prt_start", "sessionID": sid, "messageID": "msg_assistant", "type": "step-start"}},
    {"type": "text", "sessionID": sid,
     "part": {"id": "prt_text", "sessionID": sid, "messageID": "msg_assistant", "type": "text",
              "text": "Done. <Choice>pass</Choice>"}},
    {"type": "step_finish", "sessionID": sid,
     "part": {"id": "prt_finish", "sessionID": sid, "messageID": "msg_assistant", "type": "step-finish",
              "reason": "stop", "cost": 0.01,
              "tokens": {"input": 2, "output": 3, "reasoning": 0, "cache": {"read": 0, "write": 0}}}}
]
print("\n".join(json.dumps(event) for event in events))
if FRESH_NONZERO and "--session" not in args:
    raise SystemExit(7)
"""


def fake_binary(
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
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = _FAKE_OPENCODE.replace("opencode 1.18.25", f"opencode {version}")
    source = source.replace("PERMISSION_REQUEST_ONLY = False", f"PERMISSION_REQUEST_ONLY = {permission_request_only}")
    source = source.replace("PERMISSION_PROSE_ONLY = False", f"PERMISSION_PROSE_ONLY = {permission_prose_only}")
    source = source.replace("PERMISSION_DUPLICATE = False", f"PERMISSION_DUPLICATE = {permission_duplicate}")
    source = source.replace("PERMISSION_OS_ERROR = False", f"PERMISSION_OS_ERROR = {permission_os_error}")
    source = source.replace("PERMISSION_NONZERO = False", f"PERMISSION_NONZERO = {permission_nonzero}")
    source = source.replace("IGNORE_CONFIG = False", f"IGNORE_CONFIG = {ignore_config}")
    source = source.replace("DROP_CONFIG_SHELL = False", f"DROP_CONFIG_SHELL = {drop_config_shell}")
    source = source.replace("DROP_CONFIG_COMPACTION = False", f"DROP_CONFIG_COMPACTION = {drop_config_compaction}")
    source = source.replace("PROVIDER_REFUSAL = False", f"PROVIDER_REFUSAL = {provider_refusal}")
    source = source.replace(
        "CONFIGURATION_PROSE_ONLY = False", f"CONFIGURATION_PROSE_ONLY = {configuration_prose_only}"
    )
    source = source.replace("CONFIGURATION_OS_ERROR = False", f"CONFIGURATION_OS_ERROR = {configuration_os_error}")
    source = source.replace("STATIC_REPLAY = False", f"STATIC_REPLAY = {static_replay}")
    source = source.replace("FRESH_NONZERO = False", f"FRESH_NONZERO = {fresh_nonzero}")
    source = source.replace(
        "PROCESS_CONTROL_NO_LIVE_STATE = False", f"PROCESS_CONTROL_NO_LIVE_STATE = {process_control_no_live_state}"
    )
    source = source.replace(
        "TAKEOVER_WRONG_DIRECTORY = False", f"TAKEOVER_WRONG_DIRECTORY = {takeover_wrong_directory}"
    )
    source = source.replace("TAKEOVER_WRONG_SESSION = False", f"TAKEOVER_WRONG_SESSION = {takeover_wrong_session}")
    source = source.replace("TAKEOVER_NON_SSE = False", f"TAKEOVER_NON_SSE = {takeover_non_sse}")
    source = source.replace("TAKEOVER_EXIT_EARLY = False", f"TAKEOVER_EXIT_EARLY = {takeover_exit_early}")
    source = source.replace("TAKEOVER_IDLE_SSE = False", f"TAKEOVER_IDLE_SSE = {takeover_idle_sse}")
    source = source.replace("TAKEOVER_IMMEDIATE_EOF = False", f"TAKEOVER_IMMEDIATE_EOF = {takeover_immediate_eof}")
    source = source.replace("TAKEOVER_STREAM_FAILURE = False", f"TAKEOVER_STREAM_FAILURE = {takeover_stream_failure}")
    source = source.replace("TAKEOVER_EVENT_GATED = False", f"TAKEOVER_EVENT_GATED = {takeover_event_gated}")
    source = source.replace(
        "SECURITY_COMMAND_EXECUTES = False", f"SECURITY_COMMAND_EXECUTES = {security_command_executes}"
    )
    source = source.replace("MUTATE_AUTH = False", f"MUTATE_AUTH = {mutate_auth}")
    source = source.replace("READ_AUTH = False", f"READ_AUTH = {read_auth}")
    source = source.replace(
        "AUTH_READ_MARKER = None",
        f"AUTH_READ_MARKER = {str(auth_read_marker)!r}" if auth_read_marker else "AUTH_READ_MARKER = None",
    )
    source = source.replace("COMPACTION_NO_CHANGE = False", f"COMPACTION_NO_CHANGE = {compaction_no_change}")
    source = source.replace(
        "VERSION_TOUCH_PATH = None",
        f"VERSION_TOUCH_PATH = {version_touch_path.name!r}" if version_touch_path else "VERSION_TOUCH_PATH = None",
    )
    path = tmp_path / "fake-opencode"
    path.write_text(source)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(path)


__all__ = ["MODEL", "PROVIDER_SECRET", "VARIANT", "fake_binary"]
