"""Subprocess-git adapter for the selftest scratch-repo seam (package-private).

All ``subprocess``/filesystem usage for the selftest's scratch repo is confined here
(``bzh:pluggable-seams``) — the reference binding: a real ``tempfile`` directory,
``git init``-ed and seeded with one baseline commit, torn down on context-manager
exit. Mirrors the loop's own subprocess-git adapter
(``runner/loop/internal/subprocess_worktree_git.py``) in shape, over an unrelated,
throwaway repo.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.selftest.scratch_git import IScratchGit, ScratchRepo

_log = get_logger("blizzard.runner.selftest")


class ScratchGitError(RuntimeError):
    """A git operation against the selftest's scratch repo failed."""


class SubprocessScratchGit:
    """Mint a throwaway, single-commit git repo under a real temp dir, via the ``git`` CLI."""

    @contextmanager
    def new_scratch_repo(self) -> Iterator[ScratchRepo]:
        with tempfile.TemporaryDirectory(prefix="blizzard-selftest-") as tmp:
            workdir = Path(tmp)
            self._git(workdir, "init", "-q", "-b", "main")
            self._git(workdir, "config", "user.email", "selftest@blizzard.local")
            self._git(workdir, "config", "user.name", "blizzard selftest")
            (workdir / "README.md").write_text("blizzard runner selftest scratch repo\n")
            self._git(workdir, "add", "README.md")
            self._git(workdir, "commit", "-q", "-m", "selftest: scratch repo baseline")
            yield ScratchRepo(workdir=str(workdir))

    def commit_count(self, workdir: str) -> int:
        out = self._git(Path(workdir), "rev-list", "--count", "HEAD")
        try:
            return int(out.strip())
        except ValueError:
            return 0

    def _git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log.error("git failed", args=list(args), cwd=str(cwd), detail=detail)
            raise ScratchGitError(f"git {' '.join(args)} failed in {cwd}: {detail}")
        return result.stdout


def _conforms_scratch_git(x: SubprocessScratchGit) -> IScratchGit:
    return x
