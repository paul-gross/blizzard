"""The platform-behavior tiers' shared fleet world — winter-free.

The service, e2e, and crash tiers prove platform behavior, not the winter binding: their runners run on the
built-in ``basic`` provider over a ``blizzard-mock`` fixture minted without a winter source (bare ``file://``
origins plus an empty ``workspace/``). This module owns the three things every such scenario shares — the
fixture mint, the runner's workspace over it, and the scripted build prompt that works on a basic worktree.
The winter binding's own cases live in ``tests/winter/``.
"""

from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest

from blizzard.runner.config import DEFAULT_WORKSPACE_ID, HostWorkspace
from blizzard.runner.environments.provider import WorkspaceRepo

#: The fixture project repo the platform tiers drive and the env keying the disposable fixture world.
REPO_NAME = "toy-api"
FIXTURE_ENV = "e2e"
#: The branch a scripted build works on; a basic worktree arrives detached at ``origin/<base>``.
BUILD_BRANCH = "feature/scripted-build"

_FENCE_FILE = ".blizzard-mock-harness-fence"
_WINTER_SOURCE_VAR = "BLIZZARD_MOCK_WINTER_SOURCE"


class FleetWorld(NamedTuple):
    """One minted fixture world: the runner's workspace root and the bare origins the forge fronts."""

    workspace: Path
    origins: Path
    origin_bare: Path


def winter_free_env() -> dict[str, str]:
    """The ambient environment with no winter source, so no child can bind the fixture to winter."""
    return {key: value for key, value in os.environ.items() if key != _WINTER_SOURCE_VAR}


def mint_world(
    bin_dir: Path, scratch: Path, *, env: str = FIXTURE_ENV, fence: str = "fleet world fence marker\n"
) -> FleetWorld:
    """Mint a fresh, disposable fixture world under ``scratch`` and fence it for the mock harness.

    Idempotent via ``reset``. The mint names no winter source and the child environment carries none."""
    minted = subprocess.run(
        [str(bin_dir / "blizzard-mock-fixture"), "reset", "--env", env, "--scratch-root", str(scratch)],
        capture_output=True,
        text=True,
        env=winter_free_env(),
    )
    if minted.returncode != 0:
        pytest.fail(f"fixture reset exited {minted.returncode}:\n{minted.stdout}\n{minted.stderr}")
    root = scratch / env
    workspace, origins = root / "workspace", root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    assert workspace.is_dir() and origin_bare.is_dir(), "fixture mint did not lay out the expected tree"
    # Fence the tree so the mock harness will run (arbitrary code execution, gated on a marker file).
    (workspace / _FENCE_FILE).write_text(fence)
    return FleetWorld(workspace, origins, origin_bare)


def world_repos(workspace: Path) -> tuple[WorkspaceRepo, ...]:
    """Every seeded origin beside ``workspace`` (``<fixture>/origins/*.git``) as a basic-provider repo."""
    origins = workspace.parent / "origins"
    if not origins.is_dir():
        return ()
    return tuple(WorkspaceRepo(bare.stem, f"file://{bare}") for bare in sorted(origins.glob("*.git")))


def basic_workspace(workspace: Path, *, repos: tuple[WorkspaceRepo, ...] | None = None) -> HostWorkspace:
    """The runner's one workspace over ``workspace`` on the basic provider, one environment at a time."""
    return HostWorkspace(
        DEFAULT_WORKSPACE_ID,
        root=str(workspace),
        provider="basic",
        max_environments=1,
        repos=world_repos(workspace) if repos is None else repos,
        base_branch="main",
    )


def scripted_build_prompt(build: str, *, branch: str = BUILD_BRANCH, reset: bool = True) -> str:
    """``build`` — a scripted build program written against ``repo = 'toy-api'`` — made to run on a basic worktree.

    The worktree arrives detached, so the program first switches to ``branch`` (``reset`` recreates it, which a
    re-run of the node needs), and it
    locates the repo through the injected ``BLIZZARD_ENV_WORKDIRS`` rather than assuming a layout."""
    if "BLIZZARD_ENV_WORKDIRS" in build:
        return build
    repo_path = f"str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / {REPO_NAME!r})"
    build = build.replace(f"repo = {REPO_NAME!r}", f"repo = {REPO_NAME!r}\nrepo_path = {repo_path}")
    build = build.replace("pathlib.Path(repo)", "pathlib.Path(repo_path)")
    build = build.replace('["git", "-C", repo', '["git", "-C", repo_path')
    return (
        "import os, pathlib, subprocess\n"
        f"subprocess.run(['git', '-C', {repo_path}, 'switch', {'-C' if reset else '-c'!r}, {branch!r}], "
        "check=True)\n" + build
    )


def scripted_build[**P](script: Callable[P, str]) -> Callable[P, str]:
    """Decorate a function returning a scripted build program so it returns :func:`scripted_build_prompt` of it."""

    @functools.wraps(script)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> str:
        return scripted_build_prompt(script(*args, **kwargs))

    return wrapped
