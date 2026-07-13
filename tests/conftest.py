"""Shared fixtures — the two daemon runtimes, driven through one uniform surface.

The hub and the runner expose identical offline-admin surfaces (``init_environment``,
``migrate``, ``ensure_current_revision``, ``migration_runner``, ``MIGRATE_COMMAND``),
so the store tests parametrize over both trees from a single fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.runner import app as runner_app
from blizzard.runner import runtime as runner_runtime


@dataclass(frozen=True)
class Daemon:
    """One daemon's runtime + app surface, for parametrized store/app tests."""

    name: str
    runtime: ModuleType
    app: ModuleType
    build_app: Any

    def build_hosted_app(self, config: Any) -> Any:
        """The store-wired ``host`` composition root for this daemon."""
        return self.app.build_hosted_app(config)


DAEMONS = [
    Daemon("hub", hub_runtime, hub_app, hub_app.create_app_for_export),
    Daemon("runner", runner_runtime, runner_app, runner_app.create_app_for_export),
]


@pytest.fixture(params=DAEMONS, ids=[d.name for d in DAEMONS])
def daemon(request: pytest.FixtureRequest) -> Daemon:
    return request.param
