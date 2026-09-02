"""The disposable scopes and the runner-owned config one compatibility run executes inside.

Filesystem layout only: the isolated XDG roots, the model-tool shell wrappers, the runner-owned
config plus the competing project and user configs it must outrank, and the child environment that
points OpenCode at all of them.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal.opencode_proof_script import (
    CONFIG_PERMISSION_COMMAND,
    PERMISSION_AGENT,
    PERMISSION_COMMAND,
    PROCESS_CONTROL_COMMAND,
    SAFE_COMMIT_ADD_COMMAND,
    SAFE_COMMIT_COMMAND,
    SAFE_PROOF_COMMAND,
    SECURITY_DENIAL_COMMAND,
    TOOL_AGENT,
)
from blizzard.runner.harness.internal.opencode_shapes import parse_worker_config

RUNNER_CONFIG_USERNAME = "blizzard-runner-config"
COMPACTION_TAIL_TURNS = 1
PROJECT_CONFIG_SENTINEL = "project-config-sentinel"
USER_CONFIG_SENTINEL = "user-config-sentinel"
ISOLATION_EVIDENCE = {
    "config": "isolated",
    "data": "isolated",
    "state": "isolated",
    "cache": "isolated",
    "auth_discovery": "deferred until the pinned version passes",
    "auth_provisioned": False,
    "auto_update": "disabled",
}
_NON_BASH_TOOL_DENIALS = dict.fromkeys(
    (
        "apply_patch",
        "code",
        "edit",
        "execute",
        "external_directory",
        "fetch",
        "glob",
        "grep",
        "invalid",
        "lsp",
        "plan",
        "question",
        "read",
        "search",
        "skill",
        "task",
        "todo",
        "todowrite",
        "webfetch",
        "websearch",
        "write",
    ),
    "deny",
)


class OpenCodeScratchError(RuntimeError):
    """The disposable scopes or the runner-owned config could not be established."""


@dataclass(frozen=True)
class IsolationRoots:
    """Every disposable path one run owns, so no caller re-derives one by string."""

    root: Path
    home: Path
    config_dir: Path
    user_config: Path
    runner_config: Path
    data: Path
    state: Path
    cache: Path
    xdg_config: Path
    tmp: Path
    tool_bin: Path
    auth_path: Path

    @property
    def model_tool_shell(self) -> Path:
        """The shell OpenCode must resolve for its Bash tool, named once for writer and reader."""

        return self.tool_bin / "bash"

    @property
    def directories(self) -> tuple[Path, ...]:
        return (
            self.root,
            self.home,
            self.config_dir,
            self.data,
            self.state,
            self.cache,
            self.xdg_config,
            self.tmp,
            self.tool_bin,
        )

    def to_payload(self) -> dict[str, str]:
        return {field: str(getattr(self, field)) for field in _ISOLATION_FIELDS}


_ISOLATION_FIELDS = tuple(IsolationRoots.__dataclass_fields__)


@dataclass(frozen=True)
class RunnerConfig:
    """The written runner-owned config and what the probe must remember about writing it."""

    path: Path
    security_markers: tuple[Path, Path]
    snapshots: dict[Path, bytes]
    evidence: dict[str, object]


def write_runner_config(workdir: Path, roots: IsolationRoots, *, model: str, variant: str) -> RunnerConfig:
    """Write runner-owned config outside the project and conflicting configs inside isolated scopes."""

    config_path = roots.runner_config
    auth_marker = workdir / ".opencode-auth-read-marker"
    outside_marker = roots.root / "outside-write-marker"
    outside_marker.write_text("unchanged\n", encoding="ascii")
    outside_marker.chmod(0o600)
    auth_probe_command = f"cat {shlex.quote(str(roots.auth_path))} > {shlex.quote(str(auth_marker))}"
    outside_probe_command = f"printf outside > {shlex.quote(str(outside_marker))}"
    security_commands = (auth_probe_command, outside_probe_command)
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "username": RUNNER_CONFIG_USERNAME,
        "model": model,
        "variant": variant,
        "shell": str(roots.model_tool_shell),
        "permission": {
            "*": "deny",
            "bash": {
                "*": "deny",
                SAFE_PROOF_COMMAND: "allow",
                SAFE_COMMIT_ADD_COMMAND: "allow",
                SAFE_COMMIT_COMMAND: "allow",
                "sleep 5": "allow",
                PROCESS_CONTROL_COMMAND: "allow",
                PERMISSION_COMMAND: "deny",
                CONFIG_PERMISSION_COMMAND: "deny",
                SECURITY_DENIAL_COMMAND: "deny",
                security_commands[0]: "deny",
                security_commands[1]: "deny",
            },
            **_NON_BASH_TOOL_DENIALS,
        },
        "compaction": {"tail_turns": COMPACTION_TAIL_TURNS},
        "agent": {
            TOOL_AGENT: {
                "description": "Compatibility tool execution probe agent.",
                "mode": "primary",
                "prompt": (
                    "You are a deterministic compatibility probe. When the user asks for a bash tool call, "
                    "make that exact call instead of replying with text. Do not substitute a natural-language "
                    "answer for a requested tool call."
                ),
                # The top-level wildcard is a fallback rule, so the agent override must make
                # Bash visible before its command rules can allow the bounded proof commands.
                "permission": {
                    "*": "allow",
                    "bash": {
                        "*": "deny",
                        SAFE_PROOF_COMMAND: "allow",
                        SAFE_COMMIT_ADD_COMMAND: "allow",
                        SAFE_COMMIT_COMMAND: "allow",
                        "sleep 5": "allow",
                        PROCESS_CONTROL_COMMAND: "allow",
                    },
                    **_NON_BASH_TOOL_DENIALS,
                },
            },
            PERMISSION_AGENT: {
                "description": "Compatibility permission probe agent.",
                "mode": "primary",
                "prompt": (
                    "You are a deterministic compatibility probe. When the user asks for a bash tool call, "
                    "make that exact call instead of replying with text. Do not substitute a natural-language "
                    "answer for a requested tool call."
                ),
                "permission": {
                    "*": "deny",
                    "bash": {
                        "*": "deny",
                        SAFE_PROOF_COMMAND: "allow",
                        SAFE_COMMIT_ADD_COMMAND: "allow",
                        SAFE_COMMIT_COMMAND: "allow",
                        "sleep 5": "allow",
                        PROCESS_CONTROL_COMMAND: "allow",
                        PERMISSION_COMMAND: "deny",
                        CONFIG_PERMISSION_COMMAND: "deny",
                        SECURITY_DENIAL_COMMAND: "deny",
                        security_commands[0]: "deny",
                        security_commands[1]: "deny",
                    },
                    **_NON_BASH_TOOL_DENIALS,
                },
            },
        },
        "plugin": [],
    }
    # Parse the exact config shape before handing it to OpenCode; a malformed runner-owned
    # config must be a proof failure, not an unexplained provider result.
    parse_worker_config(payload)
    project_config = {
        "$schema": "https://opencode.ai/config.json",
        "model": "competing/project-model",
        "username": PROJECT_CONFIG_SENTINEL,
        "agent": {PROJECT_CONFIG_SENTINEL: {"description": "project config must not load"}},
        "permission": {"bash": "allow"},
        "plugin": [],
    }
    (workdir / "opencode.json").write_text(
        json.dumps(project_config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    user_config = roots.user_config
    user_config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    user_config.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": "competing/user-model",
                "username": USER_CONFIG_SENTINEL,
                "permission": {"bash": "allow"},
                "plugin": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    config_path.chmod(0o600)
    snapshots = {
        config_path: config_path.read_bytes(),
        workdir / "opencode.json": (workdir / "opencode.json").read_bytes(),
        user_config: user_config.read_bytes(),
    }
    evidence = {
        "kind": "runner-owned",
        "filename": config_path.name,
        "runner_path": str(config_path),
        "project_path": str(workdir / "opencode.json"),
        "user_path": str(user_config),
        "outside_project": True,
        "project_competitor": "opencode.json",
        "user_competitor": "opencode.json",
        "content_override": "OPENCODE_CONFIG_CONTENT",
        "default_permission": "deny",
        "model_tool_user": "same-user-landlock-layer",
    }
    return RunnerConfig(config_path, (auth_marker, outside_marker), snapshots, evidence)


def prepare_isolation(root: Path) -> IsolationRoots:
    """Create empty disposable OpenCode scopes for unauthenticated version preflight."""

    data = root / "data"
    roots = IsolationRoots(
        root=root,
        home=root / "home",
        config_dir=root / "config-dir",
        user_config=root / "xdg-config" / "opencode" / "opencode.json",
        runner_config=root / "runner-config" / "opencode.json",
        data=data,
        state=root / "state",
        cache=root / "cache",
        xdg_config=root / "xdg-config",
        tmp=root / "tmp",
        tool_bin=root / "tool-bin",
        auth_path=data / "opencode" / "auth.json",
    )
    for path in roots.directories:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    roots.runner_config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o711)
    roots.tool_bin.chmod(0o755)
    _write_tool_wrappers(roots.tool_bin)
    return roots


def provision_disposable_auth(roots: IsolationRoots) -> bool:
    """Copy normal OpenCode auth into disposable data only after exact preflight success."""

    normal_data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    auth_source = normal_data_home / "opencode" / "auth.json"
    auth_path = roots.auth_path
    if not auth_source.is_file():
        return False
    auth_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The runner copies the file byte-for-byte without parsing or retaining its contents;
    # the isolation root holds the only other copy, and it is removed when the run unwinds.
    with auth_source.open("rb") as source, auth_path.open("xb") as destination:
        while chunk := source.read(1024 * 1024):
            destination.write(chunk)
    auth_path.chmod(0o600)
    return True


def _write_tool_wrappers(tool_bin: Path) -> None:
    """Drop a second Landlock layer before each model-tool shell starts."""

    python = next((path for path in (Path("/usr/bin/python3"), Path("/bin/python3")) if path.is_file()), None)
    if python is None or not os.access(python, os.X_OK):
        raise OpenCodeScratchError("the OpenCode tool boundary requires a system Python interpreter")
    helper = tool_bin / "blizzard-tool-boundary.py"
    # Read as package data, not as a sibling path: the boundary source must resolve the same
    # way from a wheel, a zip import, or a checkout.
    boundary = resources.files(__package__).joinpath("opencode_tool_boundary.py")
    helper.write_text(boundary.read_text(encoding="utf-8"), encoding="utf-8")
    helper.chmod(0o600)
    for name, shell in (("bash", "/bin/bash"), ("sh", "/bin/sh")):
        path = tool_bin / name
        path.write_text(
            f'#!/bin/sh\nexec {python} {helper} {shell} "$@"\n',
            encoding="ascii",
        )
        path.chmod(0o755)


def child_env(config_path: Path | None, roots: IsolationRoots) -> dict[str, str]:
    """Build children from the allowlist and isolate config, state, cache, data, and updates."""

    env = _base_child_env()
    if config_path is not None:
        env["OPENCODE_CONFIG"] = str(config_path)
        # The serialized runner config is also supplied as content so the effective-config
        # diagnostic can compare what OpenCode resolved with what the runner wrote.
        env["OPENCODE_CONFIG_CONTENT"] = config_path.read_text(encoding="utf-8")
    env.update(
        {
            "OPENCODE_CONFIG_DIR": str(roots.config_dir),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "HOME": str(roots.home),
            "XDG_CONFIG_HOME": str(roots.xdg_config),
            "XDG_DATA_HOME": str(roots.data),
            "XDG_STATE_HOME": str(roots.state),
            "XDG_CACHE_HOME": str(roots.cache),
            "TMPDIR": str(roots.tmp),
            "BLIZZARD_OPENCODE_TEMP_ROOT": str(Path(tempfile.gettempdir()).resolve()),
            "PATH": f"{roots.tool_bin}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
    )
    return env


def _base_child_env() -> dict[str, str]:
    """Build the preflight and OpenCode environment without copying ambient Git controls."""

    env = AllowlistedEnv.of(()).variables
    env.update(
        {
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


__all__ = [
    "COMPACTION_TAIL_TURNS",
    "ISOLATION_EVIDENCE",
    "PROJECT_CONFIG_SENTINEL",
    "RUNNER_CONFIG_USERNAME",
    "USER_CONFIG_SENTINEL",
    "IsolationRoots",
    "OpenCodeScratchError",
    "RunnerConfig",
    "child_env",
    "prepare_isolation",
    "provision_disposable_auth",
    "write_runner_config",
]
