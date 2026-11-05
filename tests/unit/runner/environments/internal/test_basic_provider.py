"""Real git worktrees in an ordinary folder; no winter CLI or workspace config."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config import HostWorkspace
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.runner.environments.internal import basic_provider
from blizzard.runner.environments.internal import git as env_git_module
from blizzard.runner.environments.internal.basic_provider import BasicWorkspaceProvider
from blizzard.runner.environments.provider import WorkspaceRepo
from tests.support.child_launch import workspace_launch


class _RecordingGit:
    """A capture-git double recording its construction and every call it receives."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, ...]] = []

    def capture(self, cwd: Path, *args: str) -> str:
        self.calls.append(args)
        return ""


def test_the_basic_binding_drives_a_seam_git_bounded_by_the_clone_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed: list[_RecordingGit] = []

    def built_git(**kwargs: object) -> _RecordingGit:
        constructed.append(_RecordingGit(**kwargs))
        return constructed[-1]

    monkeypatch.setattr(env_git_module, "SubprocessEnvGit", built_git)
    launch = workspace_launch()
    settings = HostWorkspace(
        "ws", provider="basic", root=str(tmp_path / "ws"), repos=(WorkspaceRepo("toy", "file:///toy.git"),)
    ).settings(tmp_path)
    build_workspace_provider(settings, launch=launch).acquire("01JCHUNK", 1, [])

    assert [git.kwargs for git in constructed] == [
        {"children": launch.children, "env": launch.env, "timeout": basic_provider.CLONE_TIMEOUT}
    ]
    assert ("fetch", "origin", "main") in constructed[0].calls


def test_a_supplied_git_receives_the_calls(tmp_path: Path) -> None:
    injected = _RecordingGit()
    provider = BasicWorkspaceProvider(
        str(tmp_path / "ws"), repos=(WorkspaceRepo("toy", "file:///toy.git"),), git=injected
    )
    provider.acquire("01JCHUNK", 1, [])

    assert ("fetch", "origin", "main") in injected.calls
