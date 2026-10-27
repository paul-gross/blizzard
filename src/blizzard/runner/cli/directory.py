"""The host directory a runner verb names, loaded as the verb needs it."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig
from blizzard.runner.loop.mode import LoopMode


def load_host(
    directory: str, *, host: str | None = None, port: int | None = None, loop: LoopMode | None = None
) -> HostConfig:
    """``directory``'s host file, or the error that refuses it."""
    try:
        return HostConfig.load_for_store(
            Path(directory),
            host=host,
            port=port,
            loop=loop,
            flag_registry=packaged_flag_registry(),
            strict=strict_config(),
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def load_runner(directory: str, *, runner: str | None = None) -> RunnerConfig:
    """The runner ``runner`` names — by name or id, and required on a host of several — as a :class:`RunnerConfig`,
    for the offline verbs that read one runner's keys."""
    host = load_host(directory)
    try:
        return host.runner_config(runner=runner)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
