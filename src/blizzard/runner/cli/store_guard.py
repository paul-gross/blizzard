"""The guard every CLI verb that writes the runner store runs under: the host directory held, the store at the
revision this build expects, and no runner's rows foreign to the host."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass

import click

from blizzard.cli.runtime import click_exception_on
from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import dto
from blizzard.foundation.store.migrations import MigrationConnectionError, RevisionMismatchError, StoreBelowFloorError
from blizzard.runner.cli.daemon import Door, daemon_holding
from blizzard.runner.composition import build_runner_store_files
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig, RunnerEntry
from blizzard.runner.host_layout import HostHeld, HostLayout, hold_host
from blizzard.runner.hosting.roster import HostRoster
from blizzard.runner.lifecycle.identity_check import ForeignStore, foreign_store_reason, host_store_verdict
from blizzard.runner.runtime import IStoreLocation, ensure_current_revision, reset_command


def host_roster(config: IStoreLocation, clock: IClock) -> HostRoster:
    """The runners ``config``'s store names and hosts, read once; a store error propagates."""
    return build_runner_store_files(clock).host_roster(config.db_url)


def resolve_runner(config: HostConfig, runner: str | None, clock: IClock) -> RunnerEntry:
    """The runner a stopped-host verb acts on, by :meth:`HostConfig.resolve_runner`'s rule over the store's roster. A
    host with no store file yet, or a store that cannot be read, leaves the rule only the file's ``[[runner]]``s."""
    return config.resolve_runner(_tolerated_roster(config, clock), runner)


def _tolerated_roster(config: HostConfig, clock: IClock) -> HostRoster | None:
    if not HostLayout(config.root).store_path.exists():
        return None
    try:
        return host_roster(config, clock)
    except Exception:
        return None


def file_runners(config: RunnerConfig | HostConfig) -> tuple[tuple[str, str], ...]:
    """Each ``[[runner]]`` of ``config``'s host file that carries an id, as (id, name)."""
    if isinstance(config, HostConfig):
        host = config
    else:
        try:
            host = HostConfig.load(config.root, flag_registry=config.flag_registry, strict=config.strict)
        except ConfigError:
            return ()
    return tuple((entry.id, entry.name) for entry in host.runners if entry.id is not None)


@dto
@dataclass(frozen=True)
class StoreVerdict:
    """Whose store a guard found: ``foreign`` when it is not the host's, and the reset command its refusals name."""

    foreign: ForeignStore | None
    reset: str
    #: The file's identified runners, (id, name), a refusal is worded for.
    file_runners: tuple[tuple[str, str], ...] = ()


def store_reason(verdict: StoreVerdict, runner_id: str) -> str | None:
    """Why the store ``verdict`` judged is not ``runner_id``'s, or ``None`` when it is the host's."""
    if verdict.foreign is None:
        return None
    return foreign_store_reason(verdict.foreign, runner_id, verdict.reset)


def store_refusal(verdict: StoreVerdict) -> str | None:
    """The refusal a foreign store earns, worded for the file's first identified runner, else the store's first."""
    if verdict.foreign is None:
        return None
    if verdict.file_runners:
        runner_id, name = verdict.file_runners[0]
        return f"runner {name} ({runner_id}): {store_reason(verdict, runner_id)}"
    return f"the store: {store_reason(verdict, verdict.foreign.named[0])}"


def judge_store(config: RunnerConfig | HostConfig, clock: IClock) -> StoreVerdict:
    """Judge the store ``config`` names as the host's or foreign, over its identity rows, its hosted runners and the
    host file's ids, each read once."""
    runners = file_runners(config)
    roster = host_roster(config, clock)
    verdict = host_store_verdict(roster.named, roster.hosted, {runner_id for runner_id, _ in runners})
    return StoreVerdict(foreign=verdict, reset=reset_command(config.root), file_runners=runners)


@contextmanager
def store_write_guard(
    config: RunnerConfig | HostConfig,
    *,
    action: str,
    door: Door,
    clock: IClock,
    refuse_foreign: bool,
    held: Callable[[HostHeld], Exception] | None = None,
) -> Generator[StoreVerdict, None, None]:
    """The host directory held while the caller ``action``s, its store current, and the store judged the host's or
    foreign; every refusal is a :class:`click.ClickException` naming ``action``. A foreign store refuses when
    ``refuse_foreign``, else its verdict is yielded for the caller to halt every runner with. A host holding the
    directory raises ``held``'s exception when the caller gives one, else the refusal to stop it first."""
    layout = HostLayout(config.root)
    with hold_host(
        layout,
        (layout.legacy_socket_path,),
        lambda sock: daemon_holding(sock, door),
        held or (lambda running: click.ClickException(f"{running}, so this cannot {action} — stop it first")),
    ):
        with click_exception_on(RevisionMismatchError, MigrationConnectionError, StoreBelowFloorError):
            ensure_current_revision(config)
        verdict = judge_store(config, clock)
        if refuse_foreign and (refusal := store_refusal(verdict)) is not None:
            raise click.ClickException(refusal)
        yield verdict
