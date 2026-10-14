"""The host directory a runner verb names, loaded as the verb needs it."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig


def load_host(directory: str, *, host: str | None = None, port: int | None = None) -> HostConfig:
    """``directory``'s host file, or the error that refuses it — an ungraduated directory included, which names
    the graduation procedure."""
    try:
        return HostConfig.load_for_store(Path(directory), host=host, port=port)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def load_runner(directory: str) -> RunnerConfig:
    """The directory's sole runner as a :class:`RunnerConfig`, for the offline verbs that read one runner's keys."""
    host = load_host(directory)
    try:
        return host.runner_config()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
