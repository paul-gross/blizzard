"""A standalone runner over one :class:`RunnerConfig`, for the tests that drive a single runner: the host file and
the runner the production standalone opener takes, derived from that config."""

from __future__ import annotations

from typing import Unpack

from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.composition import RunnerProcess
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.host_root import OpenedHost, StandaloneOptions, build_runner_process, open_standalone
from tests.support.runner_fakes import FIXTURE_RUNNER_ID


def standalone_of(config: RunnerConfig, runner_id: str | None = None) -> tuple[HostConfig, BuiltRunner]:
    """The host file ``config``'s runner stands alone on, and the runner, bound to ``runner_id``, else to the id
    ``config`` carries, else to the fixture runner."""
    built = BuiltRunner(runner_id or config.runner_id or FIXTURE_RUNNER_ID, config)
    return HostConfig(root=config.root, db_url=config.db_url), built


def standalone_runner(
    config: RunnerConfig, *, runner_id: str | None = None, **options: Unpack[StandaloneOptions]
) -> RunnerProcess:
    """``config``'s runner on a host of one, through the production opener; closing it closes the host too."""
    return build_runner_process(*standalone_of(config, runner_id), **options)


def open_standalone_of(
    config: RunnerConfig, *, runner_id: str | None = None, **options: Unpack[StandaloneOptions]
) -> OpenedHost:
    """``config``'s runner opened on a host of one, its child not yet served."""
    return open_standalone(*standalone_of(config, runner_id), **options)


def hosted_app(
    config: RunnerConfig, *, runner_id: str | None = None, **options: Unpack[StandaloneOptions]
) -> HostedApp:
    """The served app over ``config``'s runner on a host of one; closing the app closes the host."""
    return build_hosted_app(config, open_standalone_of(config, runner_id=runner_id, **options))
