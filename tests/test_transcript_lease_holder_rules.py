"""Which shipper a transcript record's lease admits, pinned by value — no repository, no clock."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.chunk.ports.fence import EpochOwner
from blizzard.hub.domain.observability.transcripts import ships_from_lease_holder

pytestmark = pytest.mark.unit


def test_the_runner_owning_the_epoch_ships_under_it() -> None:
    assert ships_from_lease_holder(EpochOwner.runner("r1"), "r1") is True


def test_another_runners_epoch_refuses_the_shipper() -> None:
    assert ships_from_lease_holder(EpochOwner.runner("r2"), "r1") is False


def test_a_hub_owned_epoch_refuses_every_runner() -> None:
    assert ships_from_lease_holder(EpochOwner.hub(), "r1") is False


def test_an_epoch_with_no_owner_recorded_yet_admits_the_shipper() -> None:
    assert ships_from_lease_holder(None, "r1") is True
