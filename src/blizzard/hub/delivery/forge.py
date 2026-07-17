"""The forge delivery seam.

The deliver node's landing operation, behind an interface owned at the domain
(``bzh:dependency-inversion``): the coordinator lands a chunk's branch artifacts
one repo at a time (strict FIFO, D-057), and the reference binding performs a real
merge into the bare origin the fixture workspace pushed to (a single git truth —
``verification.md``). The reference stack binds the mock forge in tests and GitHub
in production (``bzh:pluggable-seams``).

Two modes: ``merge-to-main`` merges directly; ``open-pr`` opens a PR the
delivery flow then tracks to a terminal state, detecting an external merge by poll
or on-demand check. ``land`` is the P6 walking-skeleton operation;
``open_pr`` / ``check_pr`` shape the P7 PR-mode path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True)
class LandingRequest:
    """One repo's branch artifact awaiting landing."""

    repo: str
    branch_name: str
    commit_hash: str  # authoritative — pins the verified state
    base_branch: str = "main"


class LandingDisposition(StrEnum):
    """The outcome of a land attempt on one repo."""

    LANDED = "landed"
    CONFLICT = "conflict"  # merge/rebase conflict on the unlanded remainder


@dataclass(frozen=True)
class LandingResult:
    """The result of landing one repo."""

    disposition: LandingDisposition
    landed_commit: str | None  # the commit reachable from base_branch after a land
    detail: str = ""


@dataclass(frozen=True)
class PrHandle:
    """A reference to an opened PR — the open-pr mode's tracking handle."""

    repo: str
    number: int
    url: str


class PrDisposition(StrEnum):
    """A PR's terminal state as detected externally."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"  # closed without merging


@dataclass(frozen=True)
class PrState:
    """A PR's current state — poll or on-demand check result."""

    disposition: PrDisposition
    landed_commit: str | None = None


class IForgeDelivery(Protocol):
    """The delivery seam the hub coordinator lands through."""

    def land(self, request: LandingRequest) -> LandingResult:
        """Merge one repo's branch into its base, returning the landed commit."""
        ...

    def open_pr(self, request: LandingRequest) -> PrHandle:
        """Open a PR for one repo's branch (open-pr mode — D-059)."""
        ...

    def check_pr(self, handle: PrHandle) -> PrState:
        """Poll a PR's terminal state — merged, closed, still open."""
        ...
