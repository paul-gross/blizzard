"""The disposable git repository binding used by the OpenCode proof.

The compatibility diagnostic never runs in a caller's checkout. This adapter creates one
initialized repository with a baseline commit and removes the tree on context exit. It remains
separate from the selftest binding because the proofs own different seams and lifetimes.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from blizzard.runner.harness.env_allowlist import AllowlistedEnv


@dataclass(frozen=True)
class OpenCodeScratchRepo:
    """An initialized, single-commit repository for one compatibility run."""

    workdir: Path


class IOpenCodeScratchGit(Protocol):
    """Mint a disposable initialized repository for a compatibility diagnostic."""

    def new_scratch_repo(self) -> AbstractContextManager[OpenCodeScratchRepo]:
        """Return a context manager yielding an :class:`OpenCodeScratchRepo`."""
        ...

    def has_fresh_commit(self, repo: OpenCodeScratchRepo, relative_path: str, expected: str) -> bool:
        """Prove that the requested file is present in a commit, not only in the worktree."""
        ...


class OpenCodeScratchGitError(RuntimeError):
    """A disposable repository could not be initialized."""


class SubprocessOpenCodeScratchGit:
    """Create a throwaway git repository with the real local ``git`` executable."""

    @contextmanager
    def new_scratch_repo(self) -> Iterator[OpenCodeScratchRepo]:
        with tempfile.TemporaryDirectory(prefix="blizzard-opencode-") as tmp:
            workdir = Path(tmp)
            self._git(workdir, "init", "-q", "-b", "main", "--template=/dev/null")
            self._git(workdir, "config", "core.hooksPath", "/dev/null")
            self._git(workdir, "config", "user.email", "compatibility@blizzard.local")
            self._git(workdir, "config", "user.name", "blizzard compatibility")
            (workdir / "README.md").write_text("blizzard OpenCode compatibility scratch repository\n", encoding="utf-8")
            self._git(workdir, "add", "README.md")
            self._git(workdir, "commit", "-q", "-m", "compatibility: scratch repository baseline")
            yield OpenCodeScratchRepo(workdir)

    def has_fresh_commit(self, repo: OpenCodeScratchRepo, relative_path: str, expected: str) -> bool:
        """Check the worktree, committed blob, and an additional commit without ambient Git control."""

        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            return False
        try:
            committed = self._git(repo.workdir, "show", f"HEAD:{relative_path}")
            count = int(self._git(repo.workdir, "rev-list", "--count", "HEAD").strip())
            status = self._git(repo.workdir, "status", "--porcelain").splitlines()
            # The competing project config remains uncommitted for the precedence check; it is the
            # only allowed worktree artifact, and every other change invalidates the commit proof.
            clean = all(line == "?? opencode.json" for line in status)
        except (OSError, OpenCodeScratchGitError, ValueError):
            return False
        return (
            count >= 2
            and committed == expected
            and (repo.workdir / path).read_text(encoding="utf-8") == expected
            and clean
        )

    def _git(self, cwd: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-C", str(cwd), *args],
                capture_output=True,
                text=True,
                env=self._git_env(),
                start_new_session=True,
                check=False,
            )
        except OSError as exc:
            raise OpenCodeScratchGitError("failed to start git for the compatibility scratch repository") from exc
        if result.returncode != 0:
            raise OpenCodeScratchGitError("git could not initialize the compatibility scratch repository")
        return result.stdout

    @staticmethod
    def _git_env() -> dict[str, str]:
        """Use only benign process variables; ambient Git controls and hook paths never cross the seam."""

        env = AllowlistedEnv.of(()).variables
        env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return env


def _conforms_scratch_git(x: SubprocessOpenCodeScratchGit) -> IOpenCodeScratchGit:
    return x


__all__ = [
    "IOpenCodeScratchGit",
    "OpenCodeScratchGitError",
    "OpenCodeScratchRepo",
    "SubprocessOpenCodeScratchGit",
]
