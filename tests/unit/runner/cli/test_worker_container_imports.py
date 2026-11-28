"""A worker invocation builds its own command graph without pulling in the runner daemon."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.support.loaded_modules import loaded_after_running

_WORKERS = [
    ["heartbeat"],
    ["session-end"],
    ["ask", "question"],
    ["attach", "--name", "answer"],
    ["work-items", "chunk"],
    ["pm-items", "chunk"],
    ["chunk", "history"],
    ["chunk", "asks"],
    ["chunk", "notes"],
    ["artifact", "list"],
    ["artifact", "get", "answer", "--content"],
    ["artifact", "create", "--name", "answer"],
    ["artifact", "staged"],
    ["artifact", "commit", "--repo", "repo", "--branch", "branch", "--commit", "a" * 40],
    ["garden", "findings"],
    ["garden", "proposals"],
    ["finding", "list"],
    ["finding", "get", "finding"],
    ["scope", "list"],
    *[
        ["analytics", group, verb, "--since", "2026-01-01T00:00:00Z"]
        for group, verb in (
            ("counts", "files"),
            ("counts", "skills"),
            ("counts", "agent-types"),
            ("counts", "nodes"),
            ("spend", "nodes"),
            ("spend", "graphs"),
        )
    ],
    ["opencode", "drive", "--request", "/nonexistent-blizzard-test-request"],
]

_EXCLUDED = [
    ["harness", "status", "--dir", "/nonexistent-blizzard-test-runner"],
    ["settings", "get", "--dir", "/nonexistent-blizzard-test-runner"],
    ["transcript", "list", "chunk", "--dir", "/nonexistent-blizzard-test-runner"],
]

_FORBIDDEN = (
    "blizzard.runner.app",
    "blizzard.runner.composition",
    "blizzard.runner.runtime",
    "blizzard.runner.host_root",
    "blizzard.runner.store",
    "blizzard.runner.cli.runtime",
    "blizzard.runner.cli.daemon",
    "blizzard.runner.environments.internal",
)


def _forbidden(modules: list[str]) -> list[str]:
    return sorted(m for m in modules if any(m == prefix or m.startswith(prefix + ".") for prefix in _FORBIDDEN))


def _invoke(arguments: list[str], *, environ: dict[str, str] | None = None) -> dict[str, Any]:
    # Wrap the actual build, not merely command discovery or --help. Each subprocess has its own import cache.
    code = """
import json, sys
from click.testing import CliRunner
from blizzard.cli import command_root
from blizzard.cli.main import blizzard

builds = []
original = command_root.build_container
def build(*args, **kwargs):
    builds.append(kwargs.get("owner"))
    return original(*args, **kwargs)
command_root.build_container = build
result = CliRunner().invoke(blizzard, ["runner", *json.loads(sys.argv[1])])
print(json.dumps({"builds": builds, "modules": sorted(sys.modules), "output": result.output,
                  "exception": repr(result.exception), "exit_code": result.exit_code}))
"""
    env = {
        **os.environ,
        "BLIZZARD_LEASE_ID": "",
        "BLIZZARD_RUNNER_URL": "",
        "BLIZZARD_TRACEPARENT": "",
        "BLIZZARD_ELICITATION": "",
        **(environ or {}),
    }
    result = subprocess.run(
        [sys.executable, "-c", code, json.dumps(arguments)], capture_output=True, text=True, check=True, env=env
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("arguments", _WORKERS, ids=lambda args: " ".join(args[:2]))
def test_worker_invocation_constructs_its_graph_without_daemon_imports(arguments: list[str]) -> None:
    result = _invoke(arguments)
    assert result["builds"], result
    assert result["exception"] in ("None", "SystemExit(1)"), result
    assert ("cannot read the drive request" if arguments[:2] == ["opencode", "drive"] else "no BLIZZARD_") in result[
        "output"
    ], result
    assert not _forbidden(result["modules"]), result
    wiring = {m for m in result["modules"] if m.startswith("blizzard.") and m.endswith(".wiring")}
    allowed = {"blizzard.cli.wiring", "blizzard.runner.cli.wiring"}
    if arguments[:2] == ["opencode", "drive"]:
        allowed.add("blizzard.runner.harness.opencode_v2.wiring")
    assert "blizzard.runner.cli.wiring" in wiring
    assert wiring <= allowed, result
    if arguments[:2] != ["opencode", "drive"]:
        assert "blizzard.runner.harness.opencode_v2.transport" not in result["modules"], result
    else:
        assert "blizzard.runner.cli.opencode" not in result["modules"], result


@pytest.mark.parametrize("arguments", _EXCLUDED, ids=lambda args: " ".join(args[:2]))
def test_excluded_operator_leaf_does_not_construct_worker_graph(arguments: list[str]) -> None:
    result = _invoke(arguments)
    assert result["builds"] == [], result
    loaded = loaded_after_running(["runner", *arguments])
    assert "blizzard.cli.command_root" not in loaded
    assert "blizzard.runner.cli.wiring" not in loaded


@pytest.mark.parametrize("leaf", ["attend", "compatibility"])
def test_excluded_opencode_leaf_executes_without_loading_or_building_drive(leaf: str, tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    environ = {"HOME": str(tmp_path), "XDG_STATE_HOME": str(state)}
    marker = tmp_path / "version-called"
    if leaf == "attend":
        arguments = ["opencode", "attend", "ses_test"]
        expected = "no OpenCode service holds the session"
    else:
        binary = tmp_path / "opencode"
        binary.write_text(f"#!/bin/sh\nprintf called > '{marker}'\necho 999.0.0\n")
        binary.chmod(0o755)
        arguments = [
            "opencode",
            "compatibility",
            "--binary",
            str(binary),
            "--model",
            "test/model",
            "--variant",
            "test",
            "--evidence-dir",
            str(tmp_path / "evidence"),
            "--live-provider",
        ]
        expected = "outside every admitted range"

    result = _invoke(arguments, environ=environ)
    assert result["exit_code"] == 1, result
    assert expected in result["output"], result
    assert result["builds"] == [], result
    assert "blizzard.runner.cli.opencode_drive" not in result["modules"], result
    assert "blizzard.runner.cli.wiring" not in result["modules"], result
    assert "blizzard.runner.harness.opencode_v2.wiring" not in result["modules"], result
    if leaf == "compatibility":
        assert marker.read_text() == "called"


def test_import_boundary_catches_a_planted_daemon_import() -> None:
    assert _forbidden(["blizzard.runner.cli.worker", "blizzard.runner.store.internal.db"]) == [
        "blizzard.runner.store.internal.db"
    ]
