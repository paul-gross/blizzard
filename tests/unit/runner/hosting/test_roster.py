"""The host roster's store-door rule, by value."""

from __future__ import annotations

from blizzard.runner.hosting.model import HostedRoster
from blizzard.runner.hosting.roster import HostRoster


def test_a_runner_is_hosted_through_a_door_when_retired_or_hosted_beside_the_file() -> None:
    roster = HostRoster(
        hosted=frozenset({"rn_file", "rn_retired", "rn_beside", "rn_both"}),
        split=HostedRoster(active=("rn_file", "rn_beside"), retired=frozenset({"rn_retired", "rn_both"})),
        beside_the_file=frozenset({"rn_beside", "rn_both", "rn_unhosted"}),
    )
    assert roster.through_a_door() == {"rn_retired", "rn_beside", "rn_both"}


def test_an_empty_roster_holds_no_runner_through_a_door() -> None:
    assert HostRoster().through_a_door() == frozenset()


def test_a_runner_is_named_by_its_stored_name_else_its_id() -> None:
    roster = HostRoster(names={"rn_a": "alpha"})
    assert (roster.name_of("rn_a"), roster.name_of("rn_b")) == ("alpha", "rn_b")
