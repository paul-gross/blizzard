"""The guard every CLI verb that writes the runner store runs under: the host directory held, the store at the
revision this build expects, and no runner's rows foreign to the host."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import click

from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.cli.runtime import click_exception_on
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationConnectionError, RevisionMismatchError, StoreBelowFloorError
from blizzard.runner.cli.daemon import daemon_holding
from blizzard.runner.composition import build_host_stores
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_layout import HostHeld, HostLayout, hold_host
from blizzard.runner.lifecycle.identity_check import foreign_store_reason
from blizzard.runner.runtime import IStoreLocation, ensure_current_revision, reset_command
from blizzard.runner.store.errors import RunnerStoreErrorFactory


def named_runners(config: IStoreLocation) -> tuple[str, ...]:
    """The ids of the runners the store's identity rows name, oldest first — read through the cross-runner audit,
    the one read exempt from a runner's scope."""
    engine = create_engine_from_url(config.db_url)
    try:
        return build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=SystemClock()
        ).runner_audit.named_runners()
    finally:
        engine.dispose()


def configured_ids(config: RunnerConfig) -> tuple[str, ...]:
    """The ids every ``[[runner]]`` of ``config``'s host file configures — the runners a store it opens may name."""
    try:
        host = HostConfig.load(config.root, flag_registry=config.flag_registry, strict=config.strict)
    except ConfigError:
        return ()
    return tuple(entry.id for entry in host.runners if entry.id is not None)


@contextmanager
def store_write_guard(
    config: RunnerConfig | HostConfig,
    *,
    action: str,
    trace: ITraceHeaders,
    runners: Mapping[str, str],
    configured: tuple[str, ...],
    refuse_foreign: bool,
) -> Iterator[Mapping[str, str]]:
    """The host directory held while the caller ``action``s, its store current, and ``runners`` (id to name) judged
    against the store's identity rows; every refusal is a :class:`click.ClickException` naming ``action``. A foreign
    runner refuses when ``refuse_foreign``, else is yielded (id to reason) for the caller to halt."""
    layout = HostLayout(config.root)
    try:
        with hold_host(layout, (layout.legacy_socket_path,), lambda sock: daemon_holding(sock, trace)):
            with click_exception_on(RevisionMismatchError, MigrationConnectionError, StoreBelowFloorError):
                ensure_current_revision(config)
            named = named_runners(config)
            foreign = {
                runner_id: reason
                for runner_id in runners
                if (reason := foreign_store_reason(named, runner_id, reset_command(config.root), configured=configured))
                is not None
            }
            if foreign and refuse_foreign:
                runner_id, reason = next(iter(foreign.items()))
                raise click.ClickException(f"runner {runners[runner_id]} ({runner_id}): {reason}")
            yield foreign
    except HostHeld as exc:
        raise click.ClickException(f"{exc}, so this cannot {action} — stop it first") from exc
