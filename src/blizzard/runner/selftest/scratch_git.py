"""The selftest's scratch-repo seam — an isolated git repo the checks run against.

All ``subprocess``/``git`` and filesystem access is confined behind this Protocol
(``bzh:dependency-inversion``), which is what keeps a selftest run from ever touching
fleet state: the scratch repo lives and dies inside :meth:`IScratchGit.new_scratch_repo`."""

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
