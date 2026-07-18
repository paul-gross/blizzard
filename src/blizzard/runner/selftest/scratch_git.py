"""The selftest's scratch-repo seam — an isolated git repo the checks run against.

Owned here (``bzh:dependency-inversion``): the checks orchestration (:mod:`.checks`)
depends on this Protocol, never on ``subprocess``/``git`` directly;
``internal/subprocess_scratch_git.py`` is the reference binding. Confining all git
and filesystem access to one seam is what keeps a selftest run from ever touching
fleet state — the scratch repo lives and dies entirely inside
:meth:`IScratchGit.new_scratch_repo`, under a real temp directory, never a chunk's
leased environment.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ScratchRepo:
    """An initialized, single-commit scratch git repo — touches no fleet state."""

    workdir: str


class IScratchGit(Protocol):
    """Mint and inspect a throwaway git repo for the selftest's end-to-end check."""

    def new_scratch_repo(self) -> AbstractContextManager[ScratchRepo]:
        """A context manager yielding a fresh, git-initialized scratch repo, removed on exit."""
        ...

    def commit_count(self, workdir: str) -> int:
        """The number of commits reachable from ``HEAD`` in the repo at ``workdir``."""
        ...
