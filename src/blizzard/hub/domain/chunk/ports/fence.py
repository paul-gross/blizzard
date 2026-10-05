"""The write fence's vocabulary (``bzh:epoch-fencing``).

Store adapters derive the fence in the writing transaction; domain callers map a
refusal to their own failure result. The rule lives in ``blizzard-context``'s
``fencing.md``."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from blizzard.foundation.roles import domain_model


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


@domain_model
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

    @classmethod
    def displaced(cls, epoch: int, *, latest: int) -> FenceRefusal:
        """The write's attempt does not own its epoch — another party took it first."""
        return cls(epoch=epoch, latest=latest, detail=f"displaced attempt: epoch {epoch} is not this attempt's")


@domain_model
@dataclass(frozen=True)
class EpochOwner:
    """Who took one epoch of a chunk — the hub (``runner_id`` ``None``) or one runner.
    The first owner recorded for an epoch is its owner for good."""

    runner_id: str | None

    @classmethod
    def hub(cls) -> EpochOwner:
        return cls(runner_id=None)

    @classmethod
    def runner(cls, runner_id: str) -> EpochOwner:
        return cls(runner_id=runner_id)

    def is_hub(self) -> bool:
        return self.runner_id is None


@domain_model
@dataclass(frozen=True)
class Claimant:
    """The runner attempt a runner-submitted write speaks for — its runner, and the lease
    it names when it names one. A fenced seam write handed a ``claimant`` is refused unless
    this attempt owns the write's epoch; a hub-originated write passes none."""

    runner_id: str
    lease_id: str | None = None

    def owns(self, owner: EpochOwner | None, *, owning_lease_id: str | None) -> bool:
        """Whether this attempt owns an epoch owned by ``owner``. An unowned or hub-owned
        epoch is never a runner attempt's; a runner-owned one is when the runners match
        and — only when both the epoch's owning lease and this write's lease are known —
        the leases match too."""
        if owner is None or owner.is_hub() or owner.runner_id != self.runner_id:
            return False
        return owning_lease_id is None or self.lease_id is None or owning_lease_id == self.lease_id


@domain_model
@dataclass(frozen=True)
class MintAdmission:
    """Whether a runner's ``lease.minted`` at ``epoch`` may land, given the chunk's state
    read inside the minting write's own transaction."""

    epoch: int
    newest: int
    terminal: bool
    owner: EpochOwner | None
    owning_lease_id: str | None
    holds_route: bool

    def refusal(self, claimant: Claimant) -> FenceRefusal | None:
        """``None`` when the mint lands. A mint on a terminal chunk or below the newest
        epoch is refused; an owned epoch lands only for its own attempt; an unowned one
        lands only above the newest epoch, and only for the live route's holder — which
        then takes it (:meth:`takes_ownership`)."""
        if self.terminal:
            return FenceRefusal.terminal(self.epoch)
        if not EpochAdmission.AT_OR_ABOVE.admits(self.epoch, newest=self.newest):
            return FenceRefusal.stale(self.epoch, latest=self.newest)
        if self.owner is not None:
            if claimant.owns(self.owner, owning_lease_id=self.owning_lease_id):
                return None
            return FenceRefusal.displaced(self.epoch, latest=self.newest)
        if self.epoch > self.newest and self.holds_route:
            return None
        return FenceRefusal.displaced(self.epoch, latest=self.newest)

    def takes_ownership(self) -> bool:
        """Whether an admitted mint records its runner as the epoch's owner."""
        return self.owner is None
