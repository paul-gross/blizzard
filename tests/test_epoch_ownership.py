"""Epoch ownership as pure logic (unit tier) — whether a runner attempt owns an epoch, and
whether a runner's ``lease.minted`` may take one (``bzh:epoch-fencing``)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.chunks.fence import Claimant, EpochOwner, FenceRefusal, MintAdmission

pytestmark = pytest.mark.unit

_A = Claimant("r_a")
_B_OWNS = EpochOwner.runner("r_b")
_A_OWNS = EpochOwner.runner("r_a")


@pytest.mark.parametrize(
    ("owner", "owning_lease_id", "claimant", "owns"),
    [
        (None, None, _A, False),  # an unowned epoch is no runner attempt's
        (EpochOwner.hub(), None, _A, False),  # a hub owner never matches a runner write
        (_B_OWNS, None, _A, False),  # another runner's epoch
        (_A_OWNS, None, _A, True),  # the runner's own epoch, no lease known either side
        (_A_OWNS, "ls_1", _A, True),  # a write naming no lease falls back to the runner match
        (_A_OWNS, None, Claimant("r_a", "ls_1"), True),  # no owning lease bound yet
        (_A_OWNS, "ls_1", Claimant("r_a", "ls_1"), True),  # the owning lease itself
        (_A_OWNS, "ls_1", Claimant("r_a", "ls_2"), False),  # the same runner's other lease
        (EpochOwner.hub(), None, Claimant("r_a", "ls_1"), False),
    ],
)
def test_owner_matching(owner: EpochOwner | None, owning_lease_id: str | None, claimant: Claimant, owns: bool) -> None:
    assert claimant.owns(owner, owning_lease_id=owning_lease_id) is owns


def _mint(
    *,
    epoch: int,
    newest: int,
    owner: EpochOwner | None = None,
    holds_route: bool = True,
    terminal: bool = False,
    owning_lease_id: str | None = None,
) -> MintAdmission:
    return MintAdmission(
        epoch=epoch,
        newest=newest,
        terminal=terminal,
        owner=owner,
        owning_lease_id=owning_lease_id,
        holds_route=holds_route,
    )


@pytest.mark.parametrize(
    ("admission", "admitted", "takes_ownership"),
    [
        # Its own reservation, and an idempotent re-report of its own mint.
        (_mint(epoch=3, newest=3, owner=_A_OWNS), True, False),
        (_mint(epoch=3, newest=3, owner=_A_OWNS, holds_route=False), True, False),
        # An unowned epoch above the newest, from the live route's holder.
        (_mint(epoch=4, newest=3), True, True),
        (_mint(epoch=1, newest=0), True, True),
        # An unowned epoch above the newest, from a runner not holding the route.
        (_mint(epoch=4, newest=3, holds_route=False), False, True),
        # An epoch the hub or another runner owns — a restart, or another claim's reservation.
        (_mint(epoch=3, newest=3, owner=EpochOwner.hub()), False, False),
        (_mint(epoch=4, newest=4, owner=_B_OWNS), False, False),
        # Below the newest epoch, even one the runner itself owns.
        (_mint(epoch=2, newest=3, owner=_A_OWNS), False, False),
        # A terminal chunk refuses every mint.
        (_mint(epoch=4, newest=3, terminal=True), False, True),
        # The runner's own epoch whose owning lease is another of its leases.
        (_mint(epoch=3, newest=3, owner=_A_OWNS, owning_lease_id="ls_other"), True, False),
    ],
)
def test_mint_admission(admission: MintAdmission, admitted: bool, takes_ownership: bool) -> None:
    assert (admission.refusal(_A) is None) is admitted
    assert admission.takes_ownership() is takes_ownership


def test_a_mint_naming_another_lease_of_the_owning_runner_is_displaced() -> None:
    admission = _mint(epoch=3, newest=3, owner=_A_OWNS, owning_lease_id="ls_1")
    assert admission.refusal(Claimant("r_a", "ls_2")) == FenceRefusal.displaced(3, latest=3)
    assert admission.refusal(Claimant("r_a", "ls_1")) is None


@pytest.mark.parametrize(
    ("admission", "expected"),
    [
        (_mint(epoch=4, newest=3, terminal=True), FenceRefusal.terminal(4)),
        (_mint(epoch=2, newest=3, owner=_A_OWNS), FenceRefusal.stale(2, latest=3)),
        (_mint(epoch=3, newest=3, owner=EpochOwner.hub()), FenceRefusal.displaced(3, latest=3)),
        (_mint(epoch=4, newest=3, holds_route=False), FenceRefusal.displaced(4, latest=3)),
    ],
)
def test_a_refused_mint_names_why(admission: MintAdmission, expected: FenceRefusal) -> None:
    assert admission.refusal(_A) == expected


def test_a_displaced_refusal_names_the_epoch() -> None:
    assert "epoch 3" in FenceRefusal.displaced(3, latest=4).detail
