"""Shared fixtures — the two daemon runtimes, driven through one uniform surface.

The hub and the runner expose identical offline-admin surfaces, so store tests
parametrize over both via the ``daemon`` fixture. The suite-wide hooks and isolation fixtures live in
``tests/suite_plugin.py``, registered by the root ``conftest.py``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.runner import app as runner_app
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.host_config import HostConfig


@pytest.fixture(scope="session")
def spawn_executor() -> Iterator[Executor]:
    """The one executor every test ``ProcessLauncher`` forks through — the composition root's
    own single-worker shape, owned by the session rather than by any test module."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")
    try:
        yield executor
    finally:
        executor.shutdown(wait=True)


@dataclass(frozen=True)
class Daemon:
    """One daemon's runtime + app surface, for parametrized store/app tests."""

    name: str
    runtime: ModuleType
    app: ModuleType
    build_app: Any

    def build_hosted_app(self, config: Any) -> Any:
        """The store-wired ``host`` composition root for this daemon.

        The runner's returns a ``HostedApp`` carrying the served app; the hub's returns
        the served app directly. `isinstance` distinguishes the two explicitly.
        """
        if isinstance(config, HostConfig):
            # The runner's store is bound from its configured id: a host init scaffolds one that has not joined a hub.
            runner = config.runner_config()
            config = dataclasses.replace(runner, runner_id=runner.runner_id or "rn_fixture")
        built = self.app.build_hosted_app(config)
        if isinstance(built, runner_app.HostedApp):
            return built.app
        return built


DAEMONS = [
    Daemon("hub", hub_runtime, hub_app, hub_app.create_app_for_export),
    Daemon("runner", runner_runtime, runner_app, runner_app.create_app_for_export),
]


@pytest.fixture(params=DAEMONS, ids=[d.name for d in DAEMONS])
def daemon(request: pytest.FixtureRequest) -> Daemon:
    return request.param
