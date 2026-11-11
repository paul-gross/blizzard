"""One pool environment reused across two projects of one repository that start from different branches, under the
real ``winter`` CLI.

Each project's chunk names the repository's target: the overriding project's ``rust-rewrite``, the inheriting
project's ``main``. A released environment the other project's chunk reacquires starts on that chunk's target, never
on the previous tenant's. Local-only — see ``tests/winter/support.py`` for the gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from blizzard.runner.environments.internal.git import ENV_GIT_TIMEOUT
from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli, WinterCliError
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.environments.provider import RepositoryTarget
from tests.support.child_launch import CHILDREN, env_git, tool_env
from tests.winter.support import require_winter_source, winter_binding_gate

pytestmark = [pytest.mark.winter_binding, winter_binding_gate]

_OWNER, _REPO = "acme", "toy"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def _origin_with_two_branches(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A bare origin under an owner directory, holding ``main`` and a ``rust-rewrite`` one commit ahead of it."""
    origin = tmp_path / "origins" / _OWNER / f"{_REPO}.git"
    origin.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "README.md").write_text("# toy\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "switch", "-c", "rust-rewrite")
    (seed / "RUST.md").write_text("rewritten\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "rewrite")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main", "rust-rewrite")
    heads = {branch: _git(seed, "rev-parse", branch) for branch in ("main", "rust-rewrite")}
    return origin, heads


def _workspace(tmp_path: Path, origin: Path) -> Path:
    """A real winter workspace over ``origin``, addressed by a ``file://localhost`` URL so the worktree's origin names
    the repository's owner, as a forge origin does."""
    source = require_winter_source()
    workspace = tmp_path / "workspace"
    subprocess.run(["git", "clone", "--quiet", str(source), str(workspace)], check=True, capture_output=True, text=True)
    (workspace / ".winter" / "config.toml").write_text(
        f'main_branch = "main"\n\n[[project_repository]]\nname = "{_REPO}"\nurl = "file://localhost{origin}"\n'
    )
    (workspace / ".winter" / "config.lock").unlink(missing_ok=True)
    cli = SubprocessWinterCli(children=CHILDREN, env=tool_env())
    try:
        cli.ensure_ready(workspace)
        cli.run(workspace, ["ws", "init"])
    except WinterCliError as exc:  # environment-specific winter failure
        pytest.skip(f"winter ws init failed in this environment: {exc}")
    return workspace


def _target(branch: str, *, inherited: bool) -> RepositoryTarget:
    return RepositoryTarget(
        repository_id="repo_toy",
        repository_slug=_REPO,
        forge_host="localhost",
        owner=_OWNER,
        repo=_REPO,
        base_branch=branch,
        inherited=inherited,
    )


def test_a_released_env_reacquired_by_the_other_projects_chunk_starts_on_that_chunks_target(tmp_path: Path) -> None:
    origin, heads = _origin_with_two_branches(tmp_path)
    workspace = _workspace(tmp_path, origin)
    provider = WinterWorkspaceProvider(
        str(workspace),
        env_pool=["e1"],
        base_branch="main",
        winter=SubprocessWinterCli(children=CHILDREN, env=tool_env()),
        git=env_git(ENV_GIT_TIMEOUT),
    )
    overriding, inheriting = _target("rust-rewrite", inherited=False), _target("main", inherited=True)

    landed: list[tuple[str, str]] = []
    for chunk, target in (("ch_override", overriding), ("ch_inherit", inheriting), ("ch_override_again", overriding)):
        [acquired] = provider.acquire(chunk, 1, held_ids=[], targets=[target])
        repo = Path(acquired.workdir) / _REPO
        landed.append((acquired.environment_id, _git(repo, "rev-parse", "HEAD")))
        assert _git(repo, "status", "--porcelain") == ""
        (repo / "LEFT-BEHIND.md").write_text("the previous tenant's dirt\n")
        provider.release(acquired.environment_id)

    assert landed == [("e1", heads["rust-rewrite"]), ("e1", heads["main"]), ("e1", heads["rust-rewrite"])]
