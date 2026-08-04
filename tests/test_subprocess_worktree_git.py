"""Component-tier proof that the real worktree-git adapter is immune to the
detached-HEAD wedge (issue #143, Phase 6).

Drives the real ``git`` CLI (no fakes — this is the "real internal collaborators"
tier) against a worktree left in detached HEAD, and confirms ``verify`` neither reads
nor cares about the worktree's own HEAD: it takes the origin URL the environment's
repo manifest names and asks ``git ls-remote`` about it, consulting no working
directory at all.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from blizzard.runner.loop.internal.subprocess_worktree_git import SubprocessWorktreeGit
from blizzard.runner.loop.worktree import IWorktreeGit


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _current_branch_name(cwd: Path) -> str:
    # The wedge's own inference, never invoked by `verify` — read here only to assert
    # the worktree is (and remains) genuinely detached.
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()


@pytest.mark.component
def test_verify_confirms_a_declared_commit_against_a_detached_head_worktree(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    workdir = tmp_path / "toy-api"
    workdir.mkdir()
    _git(workdir, "init", "-b", "main")
    _git(workdir, "config", "user.email", "worker@example.test")
    _git(workdir, "config", "user.name", "Worker")
    (workdir / "f.txt").write_text("hello")
    _git(workdir, "add", "f.txt")
    _git(workdir, "commit", "-m", "work")
    commit = _git(workdir, "rev-parse", "HEAD").strip()
    _git(workdir, "remote", "add", "origin", f"file://{origin}")
    _git(workdir, "push", "-u", "origin", "main")

    # Detach: `--abbrev-ref HEAD` returns the literal string "HEAD" here, not a real
    # branch name.
    _git(workdir, "checkout", "--detach", commit)
    assert _current_branch_name(workdir) == "HEAD"

    verified = SubprocessWorktreeGit().verify(f"file://{origin}", "main", commit)

    assert verified is True
    # Read-only: verify never checked out a branch, never pushed, never mutated the
    # worktree's own HEAD — it is left exactly as detached as it started.
    assert _current_branch_name(workdir) == "HEAD"


@pytest.mark.component
def test_verify_rejects_a_commit_the_branch_does_not_point_at(tmp_path: Path) -> None:
    """The load-bearing check: does this branch, at this origin, point at this commit?
    A stale or invented sha fails it."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    workdir = tmp_path / "toy-api"
    workdir.mkdir()
    _git(workdir, "init", "-b", "main")
    _git(workdir, "config", "user.email", "worker@example.test")
    _git(workdir, "config", "user.name", "Worker")
    (workdir / "f.txt").write_text("hello")
    _git(workdir, "add", "f.txt")
    _git(workdir, "commit", "-m", "work")
    _git(workdir, "remote", "add", "origin", f"file://{origin}")
    _git(workdir, "push", "-u", "origin", "main")

    assert SubprocessWorktreeGit().verify(f"file://{origin}", "main", "0" * 40) is False


@pytest.mark.component
def test_verify_rejects_a_branch_that_was_never_pushed(tmp_path: Path) -> None:
    """An unpushed branch is the failure this seam exists to catch — the worker committed
    locally and declared it, but nothing reached the forge, so nothing can be delivered."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    workdir = tmp_path / "toy-api"
    workdir.mkdir()
    _git(workdir, "init", "-b", "main")
    _git(workdir, "config", "user.email", "worker@example.test")
    _git(workdir, "config", "user.name", "Worker")
    (workdir / "f.txt").write_text("hello")
    _git(workdir, "add", "f.txt")
    _git(workdir, "commit", "-m", "work")
    commit = _git(workdir, "rev-parse", "HEAD").strip()
    _git(workdir, "remote", "add", "origin", f"file://{origin}")
    _git(workdir, "push", "-u", "origin", "main")
    _git(workdir, "checkout", "-b", "feat/never-pushed")

    assert SubprocessWorktreeGit().verify(f"file://{origin}", "feat/never-pushed", commit) is False


@pytest.mark.component
def test_verify_needs_no_working_directory_at_all(tmp_path: Path) -> None:
    """Structural: the seam takes a URL, not a path. Nothing about the caller's cwd can
    change the answer — which is precisely what let a worker standing at the workspace
    root supply the workspace repo's ``origin`` for every repo alike."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    workdir = tmp_path / "toy-api"
    workdir.mkdir()
    _git(workdir, "init", "-b", "main")
    _git(workdir, "config", "user.email", "worker@example.test")
    _git(workdir, "config", "user.name", "Worker")
    (workdir / "f.txt").write_text("hello")
    _git(workdir, "add", "f.txt")
    _git(workdir, "commit", "-m", "work")
    commit = _git(workdir, "rev-parse", "HEAD").strip()
    _git(workdir, "remote", "add", "origin", f"file://{origin}")
    _git(workdir, "push", "-u", "origin", "main")

    # Run from a directory that is not a git repository and knows nothing of the origin.
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()
    cwd = Path.cwd()
    try:
        os.chdir(elsewhere)
        assert SubprocessWorktreeGit().verify(f"file://{origin}", "main", commit) is True
    finally:
        os.chdir(cwd)


@pytest.mark.component
def test_subprocess_worktree_git_has_no_push_or_head_inference_methods() -> None:
    """Structural pin (issue #143, Phase 6): the push method and the ``--abbrev-ref
    HEAD`` branch inference do not exist on either the real adapter or the
    ``IWorktreeGit`` Protocol it binds. A re-introduction fails to typecheck against
    the Protocol and raises ``AttributeError`` the instant it is called."""
    adapter = SubprocessWorktreeGit()
    for missing_attr in ("push", "find_produced_artifacts", "_current_branch"):
        assert not hasattr(IWorktreeGit, missing_attr)
        assert not hasattr(adapter, missing_attr)
        with pytest.raises(AttributeError):
            getattr(adapter, missing_attr)
