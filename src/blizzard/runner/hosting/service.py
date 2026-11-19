"""Adding a runner to the host and retiring one from it: the hub asked first, then the store written in one
transaction; the host tick starts or drains the runner.

A runner the hub mints is written to the store in one admission transaction, but the mint and the write cannot share
one — the hub mints the id. The window between them is recorded in ``bzh:crash-exemptions-runner``."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
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


@domain_model
@dataclass(frozen=True)
class NewRunner:
    """A runner to add: its name, the project ids its hub binds it to, and every settings entry its first revision
    writes — ``name`` and ``projects`` among them."""

    name: str
    projects: tuple[str, ...]
    entries: Mapping[str, Entry] = field(default_factory=dict[str, Entry])

    @classmethod
    def of(
        cls,
        name: str,
        bindings: Sequence[tuple[str, str | None]],
        settings: Mapping[str, object],
        *,
        workspaces: Sequence[str],
    ) -> NewRunner:
        """The runner ``name`` serving each (project, workspace) of ``bindings`` — a project naming no workspace runs
        in the host's only one, of its declared ``workspaces`` — with ``settings`` as its other entries."""
        only = workspaces[0] if len(workspaces) == 1 else None
        projects = [
            {"project": project, **({"workspace": chosen} if (chosen := workspace or only) else {})}
            for project, workspace in bindings
        ]
        entries = {key: Entry(value) for key, value in settings.items()}
        entries["projects"] = Entry(projects)
        return cls(name=name, projects=tuple(project for project, _ in bindings), entries=entries)


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
    _adding: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def add(self, new: NewRunner, *, actor: str, door: Door) -> AddedRunner:
        """Validate ``new`` as a settings write is, ask the hub to mint it, then write its identity, first settings
        revision, hosting and sealed token in one transaction. Adds run one at a time, so two adds of one name cannot
        both pass its check. Raises :class:`RunnerSettingsInvalid` before the hub is asked,
        :class:`HostRunnerHubRefused` on the hub's refusal of the host, and the hub client's own errors."""
        with self._adding:
            return self._add(new, actor=actor, door=door)

    def _add(self, new: NewRunner, *, actor: str, door: Door) -> AddedRunner:
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

    def held(self, runner_id: str) -> HostedRunner | None:
        """The hosted runner of ``runner_id`` and its facts, or ``None`` when the store does not hold it."""
        if runner_id not in self.hosted.hosted_runner_ids():
            return None
        return HostedRunner(runner_id, self.hosted.facts(runner_id))

    def retire(self, runner: HostedRunner, *, force: bool, actor: str, door: Door) -> RetiredRunner:
        """Ask the hub to retire ``runner``, then record its ``retired`` fact; the host tick drains it. A retired
        runner's retire writes nothing and asks nothing of the hub. Raises :class:`HostRunnerHubRefused` on the hub's
        refusal of the host, and the hub client's own errors — its refusal while the runner holds chunks among
        them."""
        runner_id = runner.runner_id
        name = self.names.names_of((runner_id,))[runner_id]
        if runner.retired():
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
        """The names the host's active runners go by, and every name its ``[[runner]]`` entries hold."""
        return frozenset(self.names.names_of(self.hosted.active()).values()) | self.names.declared_names()

    def active(self) -> tuple[tuple[str, str], ...]:
        """Each active hosted runner, (id, name), in the order it was first hosted."""
        active = self.hosted.active()
        names = self.names.names_of(active)
        return tuple((runner_id, names[runner_id]) for runner_id in active)
