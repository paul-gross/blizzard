"""Component-tier proof that the real worktree-git adapter is immune to the
detached-HEAD wedge the inference it replaced used to hit (issue #143, Phase 6).

The original bug: with a leased repo worktree in detached HEAD, the runner computed
the branch to push as the literal string ``"HEAD"`` (``git rev-parse --abbrev-ref
HEAD``) and ran ``git push --force-with-lease origin HEAD``, which git refuses —
wedging the tick loop forever. Phase 4 (`blizzard.runner.loop.internal.
subprocess_worktree_git`) deleted that inference and the push entirely, replacing them
with a read-only ``verify`` over the worker's own DECLARED branch. This drives the real
``git`` CLI (no fakes — this is the "real internal collaborators" tier) against a
worktree left in detached HEAD, the exact state that used to wedge, and confirms
``verify`` neither reads nor cares about the worktree's own HEAD at all.
"""

from __future__ import annotations

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

    # Detach: the exact state that used to wedge the runner — `--abbrev-ref HEAD`
    # returns the literal string "HEAD" here, not a real branch name.
    _git(workdir, "checkout", "--detach", commit)
    assert _current_branch_name(workdir) == "HEAD"

    verified = SubprocessWorktreeGit().verify(str(workdir), f"file://{origin}", "main", commit)

    assert verified is True
    # Read-only: verify never checked out a branch, never pushed, never mutated the
    # worktree's own HEAD — it is left exactly as detached as it started.
    assert _current_branch_name(workdir) == "HEAD"


@pytest.mark.component
@pytest.mark.parametrize(
    "cosmetic_forge",
    [
        pytest.param("{origin}/", id="trailing-slash"),
        pytest.param("{origin_no_dot_git}", id="no-dot-git-suffix"),
        pytest.param("{origin_no_dot_git}/", id="no-dot-git-suffix-and-trailing-slash"),
    ],
)
def test_verify_normalizes_a_cosmetically_different_declared_forge(tmp_path: Path, cosmetic_forge: str) -> None:
    """A declared ``forge`` that differs from ``origin`` only by a trailing ``/`` and/or
    a trailing ``.git`` still verifies (issue #143 pre-push review: the byte-exact
    equality trap). A worker's own observed ``origin`` and the runner's re-derivation of
    it are cosmetically equivalent, not necessarily byte-identical."""
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
    origin_url = f"file://{origin}"
    _git(workdir, "remote", "add", "origin", origin_url)
    _git(workdir, "push", "-u", "origin", "main")

    declared_forge = cosmetic_forge.format(origin=origin_url, origin_no_dot_git=origin_url[: -len(".git")])

    verified = SubprocessWorktreeGit().verify(str(workdir), declared_forge, "main", commit)

    assert verified is True


@pytest.mark.component
def test_verify_still_rejects_a_genuinely_different_forge(tmp_path: Path) -> None:
    """The normalization is narrow: a forge that differs by more than trailing ``/`` /
    ``.git`` cosmetics still fails ``verify`` — the drop-and-nudge path this replaced
    still holds for a real mismatch."""
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

    verified = SubprocessWorktreeGit().verify(str(workdir), "https://github.com/org/other-repo.git", "main", commit)

    assert verified is False


@pytest.mark.component
def test_subprocess_worktree_git_has_no_push_or_head_inference_methods() -> None:
    """Structural pin (issue #143, Phase 6): the wedge's own machinery — the push and
    the ``--abbrev-ref HEAD`` branch inference — no longer exists on either the real
    adapter or the ``IWorktreeGit`` Protocol it binds. A re-introduction fails to
    typecheck against the Protocol and raises ``AttributeError`` the instant it is
    called, rather than silently reintroducing the wedge."""
    adapter = SubprocessWorktreeGit()
    for missing_attr in ("push", "find_produced_artifacts", "_current_branch"):
        assert not hasattr(IWorktreeGit, missing_attr)
        assert not hasattr(adapter, missing_attr)
        with pytest.raises(AttributeError):
            getattr(adapter, missing_attr)
