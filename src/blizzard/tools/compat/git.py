"""The one subprocess seam: the repository facts the compatibility check and the change-note tool read."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """A git call failed for a reason other than an absent path."""


class Repository:
    """A git repository the tool reads through ``git``, never importing or running code from it."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _git(self, *args: str) -> str:
        # The C locale pins git's messages to English, which ``previous_tag`` reads to tell "no earlier tag" apart.
        env = {**os.environ, "LC_ALL": "C"}
        result = subprocess.run(
            ["git", "-C", str(self._root), *args], capture_output=True, text=True, timeout=30, env=env
        )
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def toplevel(self) -> Path:
        """The working tree's root."""
        return Path(self._git("rev-parse", "--show-toplevel").strip())

    def tags(self) -> list[str]:
        """Every ``v*`` tag name."""
        return self._git("tag", "--list", "v*").split()

    def tags_on_commit(self, rev: str) -> list[str]:
        """Tags pointing at the same commit as ``rev`` (including annotated tags)."""
        return self._git("tag", "--points-at", rev, "--list", "v*").split()

    def show(self, rev: str, path: str) -> str | None:
        """``path`` as ``rev`` holds it, or ``None`` when ``rev`` has no such path."""
        if not self._git("ls-tree", rev, "--", path).strip():
            return None
        return self._git("show", f"{rev}:{path}")

    def subjects(self, revision_range: str) -> list[str]:
        """The commit subjects of ``revision_range``, newest first."""
        return self._git("log", "--format=%s", revision_range).splitlines()

    def merge_base(self, left: str, right: str) -> str:
        """The best common ancestor of two revisions."""
        return self._git("merge-base", left, right).strip()

    def changed(self, base: str, head: str) -> list[tuple[str, str, str | None]]:
        """``(status letter, path, old path)`` per file differing between two revisions, renames detected.

        ``old path`` is set only for a rename, whose ``path`` is where it went."""
        out = self._git("diff", "--name-status", "-M", "-z", base, head)
        fields = out.split("\0")
        changes: list[tuple[str, str, str | None]] = []
        index = 0
        while index < len(fields) and fields[index]:
            status = fields[index][0]
            if status in "RC":
                changes.append((status, fields[index + 2], fields[index + 1]))
                index += 3
            else:
                changes.append((status, fields[index + 1], None))
                index += 2
        return changes

    def trailers(self, revision_range: str, key: str) -> list[tuple[str, list[str]]]:
        """``(sha, values of the trailer key)`` per commit of ``revision_range``, by git's own trailer parser."""
        marker = "\x1e"
        out = self._git("log", f"--format={marker}%H%n%(trailers:key={key},valueonly,unfold)", revision_range)
        commits = []
        for block in out.split(marker)[1:]:
            sha, _, rest = block.partition("\n")
            commits.append((sha, [line.strip() for line in rest.splitlines() if line.strip()]))
        return commits

    def commits(self, revision_range: str) -> list[tuple[str, str]]:
        """``(sha, subject)`` per commit of ``revision_range``, newest first."""
        return [
            (sha, subject)
            for sha, _, subject in (
                line.partition(" ") for line in self._git("log", "--format=%H %s", revision_range).splitlines()
            )
        ]

    def names(self, rev: str, directory: str) -> list[str]:
        """The file names directly under ``directory`` as ``rev`` holds it, empty when it has none."""
        listed = self._git("ls-tree", "--name-only", rev, f"{directory.rstrip('/')}/").splitlines()
        return sorted(line.rsplit("/", 1)[-1] for line in listed)

    def paths(self, rev: str, directory: str) -> list[str]:
        """Every file path under ``directory``, however deep, as ``rev`` holds it; empty when it has none."""
        return self._git("ls-tree", "-r", "--name-only", rev, "--", f"{directory.rstrip('/')}/").splitlines()

    def previous_tag(self, rev: str) -> str | None:
        """The nearest tag before ``rev``, or ``None`` when no tag precedes it; any other git failure raises."""
        if len(self._git("rev-list", "--parents", "-n", "1", rev).split()) < 2:
            return None
        try:
            return self._git("describe", "--tags", "--abbrev=0", f"{rev}^").strip()
        except GitError as exc:
            if "No names found" in str(exc) or "No tags can describe" in str(exc):
                return None
            raise
