"""The command lines the winter CLI adapter launches (component tier): real children, against stand-in ``uv`` and
``winter`` executables on a ``PATH`` that holds nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from tests.support.child_launch import CHILDREN, tool_env
from tests.support.winter_provider_fakes import FakeGit


@pytest.fixture
def launches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make stand-in ``uv`` and ``winter`` the whole ``PATH``; each launch appends ``<cwd>|<argv>`` to the log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "launches.log"
    for name in ("uv", "winter"):
        tool = bin_dir / name
        tool.write_text(
            f'#!/bin/sh\necho "$PWD|{name} $*" >> "{log}"\ncase "$*" in *capabilities*) echo "[]" ;; esac\n'
        )
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return log


def test_a_vendored_cli_acquire_launches_only_uv_run(launches: Path, tmp_path: Path) -> None:
    """With no ``mise`` on ``PATH`` and no ``mise.toml`` in the vendored CLI, an acquire completes."""
    workspace = tmp_path / "workspace"
    cli = workspace / "tools" / "winter-cli"
    cli.mkdir(parents=True)
    provider = WinterWorkspaceProvider(
        str(workspace),
        env_pool=["e1"],
        winter=SubprocessWinterCli(children=CHILDREN, env=tool_env()),
        git=FakeGit(),
    )

    provider.acquire("ch_1", 1, held_ids=[])

    run = f"{cli}|uv run --project {cli} winter"
    assert launches.read_text().splitlines() == [
        f"{run} ws pull --standalone",
        f"{run} ws init e1",
        f"{run} capabilities --json",
        f"{run} provision e1",
    ]


def test_without_a_vendored_cli_winter_runs_from_path(launches: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    SubprocessWinterCli(children=CHILDREN, env=tool_env()).run(workspace, ["ws", "init", "e1"])

    assert launches.read_text().splitlines() == [f"{workspace}|winter ws init e1"]
