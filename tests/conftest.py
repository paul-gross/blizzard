"""Shared fixtures — the two daemon runtimes, driven through one uniform surface.

The hub and the runner expose identical offline-admin surfaces (``init_environment``,
``migrate``, ``ensure_current_revision``, ``migration_runner``, ``MIGRATE_COMMAND``),
so the store tests parametrize over both trees from a single fixture.

Also makes the suite hermetic against blizzard's own worker identity vars — see
``_strip_worker_identity_env``.
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

# The identity a runner injects into every worker spawn (``ClaudeCodeAdapter._spawn_env``).
# Blizzard develops itself, so its suite routinely runs *inside* a blizzard worker, which
# inherits all of these — and a test asserting the absence of one (say, that ``attach``
# omits the token header when unauthenticated) then reads the ambient value and fails.
# ``CliRunner(env=...)`` overlays ``os.environ`` rather than replacing it, so passing a
# dict without a var does not unset it. Strip them once, for every test: a test that wants
# one sets it explicitly. Tier-gating vars (``BLIZZARD_E2E``, ``BLIZZARD_SERVICE``,
# ``BLIZZARD_CRASH_SWEEP``, ``BLIZZARD_JOURNEY``, ``BLIZZARD_MOCK_*``) are deliberately
# absent — those select which tiers run and must survive.
# ``test_runner_harness_adapter.py`` guards this list against drifting from ``_spawn_env``.
_WORKER_IDENTITY_ENV = (
    "BLIZZARD_ENV_IDS",
    "BLIZZARD_ENV_WORKDIRS",
    "BLIZZARD_SESSION_ID",
    "BLIZZARD_CHUNK_ID",
    "BLIZZARD_LEASE_ID",
    "BLIZZARD_RUNNER_URL",
    "BLIZZARD_LEASE_TOKEN",
    "BLIZZARD_RUNNER_ASK_CMD",
)


@pytest.fixture(autouse=True)
def _strip_worker_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the worker identity vars so the suite is green inside a blizzard worker."""
    for name in _WORKER_IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)


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
