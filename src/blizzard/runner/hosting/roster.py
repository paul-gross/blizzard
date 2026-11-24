"""The host's runners as one read of its store saw them — the values the stopped-host verbs decide on."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from blizzard.foundation.roles import domain_model
from blizzard.runner.hosting.model import HostedRoster, HostedRunner, hosted_beside_the_file


@domain_model
@dataclass(frozen=True)
class HostRoster:
    """What a host's store says of its runners, read once: the ids its identity rows ``named`` (oldest first), every
    runner it ``hosted`` (added or retired), the ``active``/``retired`` split, each runner's stored ``names`` by id,
    and the ids hosted ``beside_the_file`` — by a route, the page, an apply or a stopped host's ``runner add``."""

    named: tuple[str, ...] = ()
    hosted: frozenset[str] = frozenset()
    split: HostedRoster = field(default_factory=HostedRoster)
    names: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    beside_the_file: frozenset[str] = frozenset()

    @property
    def active(self) -> tuple[str, ...]:
        """The runners no fact retires, in the order they were first hosted."""
        return self.split.active

    @property
    def retired(self) -> frozenset[str]:
        """The runners a ``retired`` fact names."""
        return self.split.retired

    def name_of(self, runner_id: str) -> str:
        """``runner_id``'s stored name, else its id."""
        return self.names.get(runner_id, runner_id)

    def through_a_door(self) -> frozenset[str]:
        """The ids of the runners the store hosts through a store door — retired, or hosted beside the host file — none
        of which a ``[[runner]]`` being joined can be."""
        return frozenset(runner_id for runner_id in self.hosted if self._through_a_door(runner_id))

    def _through_a_door(self, runner_id: str) -> bool:
        return runner_id in self.retired or runner_id in self.beside_the_file


def beside_the_file_of(hosted: tuple[HostedRunner, ...]) -> frozenset[str]:
    """The ids among ``hosted`` whose facts say they were hosted other than by the host file's seed or a join."""
    return frozenset(runner.runner_id for runner in hosted if hosted_beside_the_file(runner.facts))
