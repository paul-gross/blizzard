"""A hosted runner and the append-only facts its state derives from."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.vocabulary import Door, Entry, FieldChange
from blizzard.runner.hub.identity import RunnerIdentity

#: Where a hosted-runner fact came in: the settings record's doors, plus ``hub`` for a retirement the host notices at
#: its hub rather than one it performed.
type HostedRunnerDoor = Literal["seed", "cli", "page", "api", "apply", "hub"]

#: The door and actor a fact the host's seed writes is recorded under.
SEED: Final = "seed"
#: The actor an operator's offline verb is recorded under.
OPERATOR: Final = "operator"
#: The door a CLI verb's fact is recorded under.
CLI: Final = "cli"
#: The door and actor a retirement the host notices at its hub is recorded under.
HUB: Final = "hub"
#: The door a route's fact is recorded under.
API: Final = "api"
#: The actor ``runner init``'s join of a scaffold is recorded under — the one ``cli`` fact a joining scaffold can be.
JOIN: Final = "init"


class HostedRunnerOp(StrEnum):
    """What a hosted-runner fact records."""

    ADDED = "added"
    RETIRED = "retired"


class HostedRunnerState(StrEnum):
    """A hosted runner's state: ``retired`` once any fact retires it — retirement is final — and ``active`` until
    then."""

    ACTIVE = "active"
    RETIRED = "retired"


@domain_model
@dataclass(frozen=True)
class HostedRunnerFact:
    """One fact about a hosted runner: what happened, when, who did it and through which door."""

    runner_id: str
    recorded_at: datetime
    op: HostedRunnerOp
    actor: str
    door: HostedRunnerDoor


@domain_model
@dataclass(frozen=True)
class HostedRoster:
    """The host's runners as one read saw them: the ``active`` ones in the order they were first hosted, and the
    ``retired`` ones."""

    active: tuple[str, ...] = ()
    retired: frozenset[str] = frozenset()


def state_of(facts: Sequence[HostedRunnerFact]) -> HostedRunnerState | None:
    """The state ``facts`` (oldest first) leave their runner in, or ``None`` when there are none. Retirement is final:
    a retired runner stays retired whatever follows."""
    if not facts:
        return None
    if any(fact.op is HostedRunnerOp.RETIRED for fact in facts):
        return HostedRunnerState.RETIRED
    return HostedRunnerState.ACTIVE


def hosted_beside_the_file(facts: Sequence[HostedRunnerFact]) -> bool:
    """Whether ``facts`` (oldest first) say their runner was hosted other than by the seed of a ``[[runner]]`` or
    ``init``'s join of a scaffold — added by a route, the page, an apply, or a stopped host's ``runner add`` — so a
    joining scaffold is not it."""
    if not facts or facts[0].op is not HostedRunnerOp.ADDED:
        return False
    first = facts[0]
    return first.door != SEED and not (first.door == CLI and first.actor == JOIN)


@domain_model
@dataclass(frozen=True)
class HostedRunner:
    """One runner the host holds, and the facts (oldest first) its state derives from."""

    runner_id: str
    facts: tuple[HostedRunnerFact, ...]

    @property
    def state(self) -> HostedRunnerState | None:
        return state_of(self.facts)

    def retired(self) -> bool:
        """Whether it is retired — final: a retire of it writes nothing, and no add or seed brings it back."""
        return self.state is HostedRunnerState.RETIRED


def name_taken(name: str, active_names: Collection[str]) -> str | None:
    """Why ``name`` cannot be a new runner's: a blank name, or one an active runner of the host holds; ``None`` when it
    is free. A retired runner's name is free again."""
    if not name.strip():
        return "name must be a non-blank string"
    if name.strip() in active_names:
        return f"this host already holds a runner named {name.strip()!r}; pick another name"
    return None


@domain_model
@dataclass(frozen=True)
class HostingFact:
    """Who hosts a runner and through which door, stamped by the writer's injected clock."""

    actor: str
    door: HostedRunnerDoor
    at: datetime


@domain_model
@dataclass(frozen=True)
class RunnerAdmission:
    """Everything one runner's add writes in one transaction: its identity row and first settings revision (the
    ``entries`` and the ``create`` change naming them), its hosted row and ``added`` fact, and its sealed token —
    every row stamped ``at``, by ``actor`` through ``door``."""

    identity: RunnerIdentity
    entries: Mapping[str, Entry]
    actor: str
    door: Door
    at: datetime

    def hosting(self) -> HostingFact:
        return HostingFact(actor=self.actor, door=self.door, at=self.at)

    def diff(self) -> tuple[FieldChange, ...]:
        """The ``create`` change: every entry moved from absent, in key order."""
        return tuple(FieldChange(key, None, self.entries[key]) for key in sorted(self.entries))
