"""The write fence's vocabulary (``bzh:epoch-fencing``).

A store adapter derives the fence inside the transaction that records a write and answers
with these types; the domain caller maps a refusal to its own failure result. The rule
itself lives in ``blizzard-context``'s ``fencing.md`` — this module only names the epochs
a write admits and the reason one was refused."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EpochAdmission(Enum):
    """Which epochs a write accepts, against the chunk's newest."""

    CURRENT = "current"  # the epoch must equal the newest
    AT_OR_ABOVE = "at_or_above"  # refuses only an epoch below the newest
    ABOVE = "above"  # refuses an epoch at or below the newest

    def admits(self, epoch: int, *, newest: int) -> bool:
        """Whether ``epoch`` clears the fence at ``newest`` — ``0`` meaning no epoch has
        been minted yet, which every admission accepts."""
        if newest == 0:
            return True
        if self is EpochAdmission.CURRENT:
            return epoch == newest
        if self is EpochAdmission.AT_OR_ABOVE:
            return epoch >= newest
        return epoch > newest


@dataclass(frozen=True)
class FenceRefusal:
    """Why a fenced write was refused and never recorded: the chunk is terminal, or the
    write's epoch is stale against ``latest``."""

    epoch: int
    latest: int | None  # ``None`` — the refusal is terminality, not staleness
    detail: str

    @classmethod
    def terminal(cls, epoch: int) -> FenceRefusal:
        return cls(epoch=epoch, latest=None, detail="chunk is terminal")

    @classmethod
    def stale(cls, epoch: int, *, latest: int) -> FenceRefusal:
        return cls(epoch=epoch, latest=latest, detail=f"stale epoch {epoch}; chunk is at {latest}")
