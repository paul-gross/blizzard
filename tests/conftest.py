"""Shared fixtures — the two daemon runtimes, driven through one uniform surface.

The hub and the runner expose identical offline-admin surfaces, so store tests
parametrize over both via the ``daemon`` fixture, whose ``Daemon`` lives in
``tests/support/daemons.py``. The suite-wide hooks and isolation fixtures live in
``tests/support/suite_plugin.py``, registered by the root ``conftest.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Executor, ThreadPoolExecutor

import pytest

from tests.support.daemons import DAEMONS, Daemon
from tests.support.scaffolding import refused_address


@pytest.fixture
def refused_url() -> Iterator[str]:
    """An ``http://127.0.0.1:PORT`` URL nothing listens on — a connect is refused — held for the test."""
    with refused_address() as url:
        yield url


@pytest.fixture(scope="session")
def spawn_executor() -> Iterator[Executor]:
    """The one executor every test ``ProcessLauncher`` forks through — the composition root's
    own single-worker shape, owned by the session rather than by any test module."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")
    try:
        yield executor
    finally:
        executor.shutdown(wait=True)


@pytest.fixture(params=DAEMONS, ids=[d.name for d in DAEMONS])
def daemon(request: pytest.FixtureRequest) -> Daemon:
    return request.param
