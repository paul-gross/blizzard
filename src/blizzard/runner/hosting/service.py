"""Adding a runner to the host and retiring one from it: the hub asked first, then the store written in one
transaction; the host tick starts or drains the runner.

A runner the hub mints is written to the store in one admission transaction, but the mint and the write cannot share
one — the hub mints the id. The window between them is recorded in ``bzh:crash-exemptions-runner``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.vocabulary import Door, Entry
from blizzard.runner.hosting.model import HostedRunner, HostedRunnerOp, HostingFact, RunnerAdmission, name_taken
from blizzard.runner.hosting.ports import (
    IHostedRunnerNames,
    IHostRunnerHub,
    INewRunnerSettings,
    IRunnerAdmissions,
    IRunnerTokenSealer,
    IWriteHostedRunnerRepository,
)
from blizzard.runner.hub.client import HostRefused
from blizzard.runner.hub.identity import RunnerIdentity

_CP_ADD_AFTER_MINT = crashpoint(
    "hostrunner.add.after-mint.before-rows",
    "the hub minted the runner under the host; the host's store holds nothing of it yet",
)
_CP_ADD_AFTER_ROWS = crashpoint(
    "hostrunner.add.after-rows.before-start",
    "the runner's identity, settings, hosting and sealed token are in the store; the host has not started it",
)
_CP_RETIRE_AFTER_HUB = crashpoint(
    "hostrunner.retire.after-hub.before-fact",
    "the hub retired the runner; the store holds it active",
)
_CP_RETIRE_AFTER_FACT = crashpoint(
    "hostrunner.retire.after-fact.before-drain",
    "the store holds the runner retired; the host still runs it and holds its sealed token",
)

_log = get_logger("blizzard.runner.hosting")


class RunnerSettingsInvalid(Exception):
    """A new runner's settings would not resolve; nothing was asked of the hub. ``problems`` names every failing key."""

    def __init__(self, problems: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{key}: {why}" for key, why in sorted(problems.items())))
        self.problems = dict(problems)


class HostRunnerHubRefused(Exception):
    """The hub refused the host's own token, or answered not-found, so it neither added nor retired the runner."""

    def __init__(self, refused: HostRefused) -> None:
        super().__init__(refused.detail)
        self.refused = refused


class HostedRunnerUnknown(Exception):
    """The host holds no runner of the id."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"this host holds no runner {runner_id}")
        self.runner_id = runner_id


@domain_model
@dataclass(frozen=True)
class NewRunner:
    """A runner to add: its name, the project ids its hub binds it to, and every settings entry its first revision
    writes — ``name`` and ``projects`` among them."""

    name: str
    projects: tuple[str, ...]
    entries: Mapping[str, Entry] = field(default_factory=dict[str, Entry])


@domain_model
@dataclass(frozen=True)
class AddedRunner:
    """A runner the hub minted and the store holds, for the host tick to start."""

    runner_id: str
    name: str


@domain_model
@dataclass(frozen=True)
class RetiredRunner:
    """A runner retired: the chunks the hub released from it, and whether it was retired already, so nothing was
    written."""

    runner_id: str
    name: str
    released_chunk_ids: tuple[str, ...] = ()
    already: bool = False


@dataclass(frozen=True)
class HostedRunnerService:
    """Adds and retires the host's runners: the hub asked under the host's token, then the store written."""

    hosted: IWriteHostedRunnerRepository
    admissions: IRunnerAdmissions
    settings: INewRunnerSettings
    names: IHostedRunnerNames
    tokens: IRunnerTokenSealer
    hub: IHostRunnerHub
    clock: IClock

    def add(self, new: NewRunner, *, actor: str, door: Door) -> AddedRunner:
        """Validate ``new`` as a settings write is, ask the hub to mint it, then write its identity, first settings
        revision, hosting and sealed token in one transaction. Raises :class:`RunnerSettingsInvalid` before the hub is
        asked, :class:`HostRunnerHubRefused` on the hub's refusal of the host, and the hub client's own errors."""
        name = new.name.strip()
        entries = {**new.entries, "name": Entry(name)}
        problems = dict(self.settings.problems(name, entries))
        if (taken := name_taken(name, self.active_names())) is not None:
            problems["name"] = f"{problems['name']}; {taken}" if "name" in problems else taken
        if problems:
            raise RunnerSettingsInvalid(problems)
        answer = self.hub.add_runner(name, projects=new.projects)
        if isinstance(answer, HostRefused):
            raise HostRunnerHubRefused(answer)
        _CP_ADD_AFTER_MINT.reached()
        at = self.clock.now()
        self.admissions.admission(answer.runner_id).admit(
            RunnerAdmission(
                identity=RunnerIdentity(runner_id=answer.runner_id, runner_name=name, registered_at=at),
                entries=entries,
                actor=actor,
                door=door,
                at=at,
            ),
            self.tokens.seal_first(answer.runner_id, answer.token),
        )
        _CP_ADD_AFTER_ROWS.reached()
        _log.info("runner added", runner_id=answer.runner_id, door=door, actor=actor)
        return AddedRunner(runner_id=answer.runner_id, name=name)

    def retire(self, runner_id: str, *, force: bool, actor: str, door: Door) -> RetiredRunner:
        """Ask the hub to retire ``runner_id``, then record its ``retired`` fact; the host tick drains it. A retired
        runner's retire writes nothing and asks nothing of the hub. Raises :class:`HostedRunnerUnknown` for a runner
        the store does not hold, :class:`HostRunnerHubRefused` on the hub's refusal of the host, and the hub client's
        own errors — its refusal while the runner holds chunks among them."""
        if runner_id not in self.hosted.hosted_runner_ids():
            raise HostedRunnerUnknown(runner_id)
        name = self.names.name_of(runner_id)
        if HostedRunner(runner_id, self.hosted.facts(runner_id)).retired():
            return RetiredRunner(runner_id=runner_id, name=name, already=True)
        answer = self.hub.retire_runner(runner_id, force=force)
        if isinstance(answer, HostRefused):
            raise HostRunnerHubRefused(answer)
        _CP_RETIRE_AFTER_HUB.reached()
        self.hosted.append_fact(
            runner_id, HostedRunnerOp.RETIRED, HostingFact(actor=actor, door=door, at=self.clock.now())
        )
        _CP_RETIRE_AFTER_FACT.reached()
        _log.info("runner retired", runner_id=runner_id, door=door, actor=actor, force=force)
        return RetiredRunner(runner_id=runner_id, name=name, released_chunk_ids=answer.released_chunk_ids)

    def active_names(self) -> frozenset[str]:
        """The names the host's active runners go by, and its ``[[runner]]`` entries not hosted yet."""
        return (
            frozenset(self.names.name_of(runner_id) for runner_id in self.hosted.active()) | self.names.unhosted_names()
        )

    def active(self) -> tuple[tuple[str, str], ...]:
        """Each active hosted runner, (id, name), in the order it was first hosted."""
        return tuple((runner_id, self.names.name_of(runner_id)) for runner_id in self.hosted.active())
