"""The forge delivery seam (D-030/D-059/D-065).

The deliver node's landing operation, behind an interface owned at the domain
(``bzh:dependency-inversion``): the coordinator lands a chunk's branch artifacts
one repo at a time (strict FIFO, D-057), and the reference binding performs a real
merge into the bare origin the fixture workspace pushed to (a single git truth —
``verification.md``). The reference stack binds the mock forge in tests and GitHub
in production (``bzh:pluggable-seams``).

Two modes (D-059): ``merge-to-main`` merges directly; ``open-pr`` opens a PR the
delivery flow then tracks to a terminal state, detecting an external merge by poll
or on-demand check (D-065). ``land`` is the P6 walking-skeleton operation;
``open_pr`` / ``check_pr`` shape the P7 PR-mode path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True)
class LandingRequest:
    """One repo's branch artifact awaiting landing (D-026/D-091)."""

    repo: str
    branch_name: str
    commit_hash: str  # authoritative — pins the verified state (D-060)
    base_branch: str = "main"


class LandingDisposition(StrEnum):
    """The outcome of a land attempt on one repo."""

    LANDED = "landed"
    CONFLICT = "conflict"  # merge/rebase conflict on the unlanded remainder (D-086)


@dataclass(frozen=True)
class LandingResult:
    """The result of landing one repo (D-091)."""

    disposition: LandingDisposition
    landed_commit: str | None  # the commit reachable from base_branch after a land
    detail: str = ""


@dataclass(frozen=True)
class PrHandle:
    """A reference to an opened PR (D-059) — the open-pr mode's tracking handle."""

    repo: str
    number: int
    url: str


class PrDisposition(StrEnum):
    """A PR's terminal state as detected externally (D-065)."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"  # closed without merging


@dataclass(frozen=True)
class PrState:
    """A PR's current state (D-065) — poll or on-demand check result."""

    disposition: PrDisposition
    landed_commit: str | None = None


class IForgeDelivery(Protocol):
    """The delivery seam the hub coordinator lands through (D-030)."""

    def land(self, request: LandingRequest) -> LandingResult:
        """Merge one repo's branch into its base, returning the landed commit (D-091)."""
        ...

    def open_pr(self, request: LandingRequest) -> PrHandle:
        """Open a PR for one repo's branch (open-pr mode — D-059)."""
        ...

    def check_pr(self, handle: PrHandle) -> PrState:
        """Poll a PR's terminal state — merged, closed, still open (D-065)."""
        ...
