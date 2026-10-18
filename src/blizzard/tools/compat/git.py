"""The one subprocess seam: the repository facts the compatibility check reads."""

from __future__ import annotations

import subprocess
from pathlib import Path

from blizzard.foundation.roles import collaborator


class GitError(RuntimeError):
    """A git call failed for a reason other than an absent path."""


@collaborator
class Repository:
    """A git repository the tool reads through ``git``, never importing or running code from it."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self._root), *args], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

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
