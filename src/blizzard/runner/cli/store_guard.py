"""The guard every CLI verb that writes the runner store runs under: the host directory held, the store at the
revision this build expects, and no runner's rows foreign to the host."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass

import click

from blizzard.cli.runtime import click_exception_on
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import dto
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationConnectionError, RevisionMismatchError, StoreBelowFloorError
from blizzard.runner.cli.daemon import Door, daemon_holding
from blizzard.runner.composition import build_host_stores, build_settings_stores
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig, RunnerEntry
from blizzard.runner.host_layout import HostHeld, HostLayout, hold_host
from blizzard.runner.hosting.model import hosted_beside_the_file
from blizzard.runner.hosting.names import stored_name
from blizzard.runner.lifecycle.identity_check import ForeignStore, foreign_store_reason, host_store_verdict
from blizzard.runner.runtime import IStoreLocation, ensure_current_revision, reset_command
from blizzard.runner.store.errors import RunnerStoreErrorFactory


def named_runners(config: IStoreLocation, clock: IClock) -> tuple[str, ...]:
    """The ids of the runners the store's identity rows name, oldest first."""
    engine = create_engine_from_url(config.db_url)
    try:
        return build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=clock
        ).runner_audit.named_runners()
    finally:
        engine.dispose()


def hosted_runner_ids(config: IStoreLocation, clock: IClock) -> frozenset[str]:
    """The ids of every runner the store hosts, added or retired."""
    engine = create_engine_from_url(config.db_url)
    try:
        return build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=clock
        ).hosted_runners.hosted_runner_ids()
    finally:
        engine.dispose()


def store_only_entries(config: HostConfig, clock: IClock) -> tuple[RunnerEntry, ...]:
    """The entry of each runner the store hosts, not retired, that no ``[[runner]]`` names — built from its stored
    ``name`` — in the order it was hosted; none when the host has no store file yet."""
    if not HostLayout(config.root).store_path.exists():
        return ()
    named = {entry.id for entry in config.runners if entry.id is not None}
    errors = RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
    engine = create_engine_from_url(config.db_url)
    try:
        entries: list[RunnerEntry] = []
        for runner_id in build_host_stores(engine, errors=errors, clock=clock).hosted_runners.active():
            if runner_id not in named:
                identity, settings = build_settings_stores(engine, errors=errors, runner_id=runner_id)
                entries.append(config.hosted_entry(runner_id, stored_name(identity, settings, runner_id)))
        return tuple(entries)
    finally:
        engine.dispose()


def resolve_runner(config: HostConfig, runner: str | None, clock: IClock) -> RunnerEntry:
    """The runner a stopped-host verb acts on, by name or id, else the host's only one: a ``[[runner]]``, or a runner
    the store hosts that no ``[[runner]]`` names, by its stored ``name``. Raises :class:`ConfigError` as
    :meth:`HostConfig.sole_runner` does when neither holds it."""
    try:
        return config.sole_runner(runner)
    except ConfigError as refused:
        try:
            stored = store_only_entries(config, clock)
        except Exception:
            raise refused from None
        if runner is not None:
            matched = [entry for entry in stored if runner in (entry.name, entry.id)]
        elif not config.runners:
            matched = list(stored)
        else:
            matched = []
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            held = ", ".join(f"{entry.name} ({entry.id})" for entry in matched)
            raise ConfigError(
                f"the store at {config.db_url} hosts several runners ({held}); name one with --runner"
            ) from None
        raise


def store_hosted_runner_ids(config: IStoreLocation, clock: IClock) -> frozenset[str]:
    """The ids of the runners the store hosts through a store door — retired, or hosted beside the host file — none of
    which a ``[[runner]]`` being joined can be."""
    engine = create_engine_from_url(config.db_url)
    try:
        hosted = build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=clock
        ).hosted_runners
        return frozenset(
            runner_id
            for runner_id in hosted.hosted_runner_ids()
            if runner_id in hosted.retired() or hosted_beside_the_file(hosted.facts(runner_id))
        )
    finally:
        engine.dispose()


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
    verdict = host_store_verdict(
        named_runners(config, clock), hosted_runner_ids(config, clock), {runner_id for runner_id, _ in runners}
    )
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
