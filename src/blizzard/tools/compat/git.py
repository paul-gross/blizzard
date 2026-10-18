"""The one subprocess seam: the repository facts the compatibility check and the change-note tool read."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """A git call failed for a reason other than an absent path."""


class Repository:
    """A git repository the tool reads through ``git``, never importing or running code from it."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self._root), *args], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def toplevel(self) -> Path:
        """The working tree's root."""
        return Path(self._git("rev-parse", "--show-toplevel").strip())

    def tags(self) -> list[str]:
        """Every ``v*`` tag name."""
        return self._git("tag", "--list", "v*").split()

    def show(self, rev: str, path: str) -> str | None:
        """``path`` as ``rev`` holds it, or ``None`` when ``rev`` has no such path."""
        if not self._git("ls-tree", rev, "--", path).strip():
            return None
        return self._git("show", f"{rev}:{path}")

    def subjects(self, revision_range: str) -> list[str]:
        """The commit subjects of ``revision_range``, newest first."""
        return self._git("log", "--format=%s", revision_range).splitlines()

    def names(self, rev: str, directory: str) -> list[str]:
        """The file names directly under ``directory`` as ``rev`` holds it, empty when it has none."""
        listed = self._git("ls-tree", "--name-only", rev, f"{directory.rstrip('/')}/").splitlines()
        return sorted(line.rsplit("/", 1)[-1] for line in listed)

    def previous_tag(self, rev: str) -> str | None:
        """The nearest tag before ``rev``, or ``None`` when no tag precedes it."""
        try:
            return self._git("describe", "--tags", "--abbrev=0", f"{rev}^").strip()
        except GitError:
            return None
