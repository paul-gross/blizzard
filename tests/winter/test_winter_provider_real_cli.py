"""The winter workspace provider against the real ``winter`` CLI and git, over a minimal ``file://`` workspace.

The winter binding's own cases: the platform tiers run on the basic provider, so the only place the real CLI
drives an allocation is here. Local-only — see ``tests/winter/support.py`` for the gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from blizzard.runner.environments.internal.git import ENV_GIT_TIMEOUT
from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli, WinterCliError
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from tests.support_child_launch import CHILDREN, env_git, tool_env
from tests.winter.support import require_winter_source, winter_binding_gate

pytestmark = [pytest.mark.winter_binding, winter_binding_gate]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _git_out(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _make_workspace(tmp_path: Path, repos: tuple[str, ...] = ("toy",)) -> Path:
    """A real winter workspace (cloned framework) over bare file:// origins with a main commit each."""
    entries: list[str] = ['main_branch = "main"']
    for name in repos:
        origin = tmp_path / "origins" / f"{name}.git"
        origin.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
        seed = tmp_path / "seeds" / name
        seed.mkdir(parents=True)
        _git(seed, "init", "-b", "main")
        _git(seed, "config", "user.email", "t@t")
        _git(seed, "config", "user.name", "t")
        (seed / "README.md").write_text(f"# {name}\n")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-m", "seed")
        _git(seed, "remote", "add", "origin", str(origin))
        _git(seed, "push", "origin", "main")
        entries.append(f'[[project_repository]]\nname = "{name}"\nurl = "file://{origin}"')

    source = require_winter_source()
    workspace = tmp_path / "workspace"
    subprocess.run(["git", "clone", "--quiet", str(source), str(workspace)], check=True, capture_output=True, text=True)
    (workspace / ".winter" / "config.toml").write_text("\n\n".join(entries) + "\n")
    lock = workspace / ".winter" / "config.lock"
    if lock.exists():
        lock.unlink()
    return workspace


def _init_workspace_or_skip(workspace: Path) -> None:
    """Workspace-level init (source checkouts off the file:// origins) via the vendored CLI."""
    cli = SubprocessWinterCli(children=CHILDREN, env=tool_env())
    try:
        cli.ensure_ready(workspace)
        cli.run(workspace, ["ws", "init"])
    except WinterCliError as exc:  # environment-specific winter failure
        pytest.skip(f"winter ws init failed in this environment: {exc}")


def test_real_winter_acquire_returns_clean_worktree_and_resets(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _init_workspace_or_skip(workspace)

    provider = WinterWorkspaceProvider(
        str(workspace),
        env_pool=["e1"],
        base_branch="main",
        winter=SubprocessWinterCli(children=CHILDREN, env=tool_env()),
        git=env_git(ENV_GIT_TIMEOUT),
    )
    acquired = provider.acquire("ch_1", 1, held_ids=[])
    assert len(acquired) == 1
    repo = Path(acquired[0].workdir) / "toy"
    assert (repo / "README.md").exists()  # a real, materialized worktree

    # Reset-on-acquire erases the previous tenant's dirt on the next acquire.
    (repo / "junk.txt").write_text("left behind")
    (repo / "README.md").write_text("dirtied")
    provider.acquire("ch_1", 1, held_ids=[])
    assert not (repo / "junk.txt").exists()
    assert (repo / "README.md").read_text() == "# toy\n"


def test_real_winter_acquire_recovers_stale_feature_branch_tracking(tmp_path: Path) -> None:
    """A previous tenant leaves one repo connected to a feature branch that exists only
    for it while its sibling sits unconnected — re-acquire must still
    succeed."""
    workspace = _make_workspace(tmp_path, repos=("toy-a", "toy-b"))
    _init_workspace_or_skip(workspace)

    provider = WinterWorkspaceProvider(
        str(workspace),
        env_pool=["e1"],
        base_branch="main",
        winter=SubprocessWinterCli(children=CHILDREN, env=tool_env()),
        git=env_git(ENV_GIT_TIMEOUT),
    )
    workdir = Path(provider.acquire("ch_1", 1, held_ids=[])[0].workdir)
    repo_a, repo_b = workdir / "toy-a", workdir / "toy-b"

    # The previous tenant: toy-a delivered from a feature branch (pushed, upstream set —
    # the ref exists only for toy-a); toy-b left unconnected, dirty, with untracked junk.
    _git(repo_a, "config", "user.email", "t@t")
    _git(repo_a, "config", "user.name", "t")
    _git(repo_a, "checkout", "-b", "fix/stale")
    (repo_a / "work.txt").write_text("wip")
    _git(repo_a, "add", "-A")
    _git(repo_a, "commit", "-m", "wip")
    _git(repo_a, "push", "-u", "origin", "fix/stale")
    (repo_b / "README.md").write_text("dirtied")
    (repo_b / "junk.txt").write_text("left behind")

    # Re-acquire must succeed.
    reacquired = provider.acquire("ch_2", 1, held_ids=[])
    assert Path(reacquired[0].workdir) == workdir

    for repo, readme in ((repo_a, "# toy-a\n"), (repo_b, "# toy-b\n")):
        # Disconnected: no worktree retains feature-branch upstream tracking.
        assert _git_out(repo, "rev-parse", "--abbrev-ref", "@{u}").returncode != 0
        # Forced to origin/main and clean: no dirty or untracked files survive.
        assert _git_out(repo, "status", "--porcelain").stdout.strip() == ""
        assert (repo / "README.md").read_text() == readme
    assert not (repo_a / "work.txt").exists()
    assert not (repo_b / "junk.txt").exists()
