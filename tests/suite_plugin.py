"""Suite-wide pytest wiring, registered by the root ``conftest.py`` so it reaches every collected directory:
the tier-marker record, store-revision retirement, sharding, the statement guards, and the autouse isolation fixtures."""

from __future__ import annotations

import os
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.config_keys import STRICT_CONFIG_ENVIRONMENT_VARIABLE
from blizzard.foundation.operator_sessions.internal import session_file
from blizzard.foundation.runner_token_names import HUB_TOKEN_VARIABLE_PREFIX
from blizzard.hub.domain.config.legacy_keys import LEGACY_FORGE_VARIABLES
from blizzard.hub.store.tenant_guard import GUARD_ENVIRONMENT_VARIABLE, install_tenant_guard
from blizzard.runner.config import ENV_HUB_URL
from blizzard.runner.store.runner_guard import GUARD_ENVIRONMENT_VARIABLE as RUNNER_GUARD_ENVIRONMENT_VARIABLE
from blizzard.runner.store.runner_guard import install_runner_guard
from tests.head_replay_guard import install_head_replay_guard
from tests.repo_files import install_repo_read_guard
from tests.revision_age import (
    InvalidRevisionTest,
    Retired,
    colocated_versions,
    decide_colocated,
)

# Install before test-module imports so collection-time reads are covered too.
install_repo_read_guard()
install_head_replay_guard()


#: The seven verification tiers declared in ``pyproject.toml``'s ``markers`` list.
_TIER_MARKERS = frozenset(
    {"unit", "component", "service", "e2e", "crash_sweep", "journey", "rehearsal", "winter_binding"}
)

#: Every collected item's nodeid missing every tier marker, populated by
#: ``pytest_collection_modifyitems`` below. A normal test can't see its siblings'
#: markers on its own, so ``tests/test_default_suite_markers.py`` reads this back instead.
UNMARKED_TEST_NODEIDS: list[str] = []


#: ``<index>/<count>`` (1-based) keeps only that shard of the collected suite, so CI splits
#: one tier across parallel jobs; unset keeps everything.
SHARD_ENV = "BLIZZARD_TEST_SHARD"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Record every collected test that carries none of the tier markers, then keep only
    this run's shard.

    An unmarked test still runs in the default suite but is invisible to both ``-m
    unit`` and ``-m component`` -- a gap neither tier's total would catch. The record is
    taken before sharding, so whichever shard runs the marker check sees the whole suite.
    """
    UNMARKED_TEST_NODEIDS[:] = [
        item.nodeid for item in items if not (_TIER_MARKERS & {mark.name for mark in item.iter_markers()})
    ]
    _retire_aged_revision_tests(items)
    spec = os.environ.get(SHARD_ENV)
    if not spec:
        return
    index, count = parse_shard(spec)
    deselected = [item for item in items if shard_of(item.nodeid, count) != index]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if shard_of(item.nodeid, count) == index]


def _retire_aged_revision_tests(items: list[pytest.Item]) -> None:
    """Skip each colocated store-migration test whose revision has aged out; refuse a file naming no sibling revision.

    Runs before sharding, so a bad name fails every shard and shard membership stays a nodeid hash.
    """
    today = datetime.now(UTC).date()
    decided: dict[Path, Retired | None] = {}
    for item in items:
        if colocated_versions(item.path) is None:
            continue
        if item.path not in decided:
            try:
                decided[item.path] = decide_colocated(item.path, today=today, environ=os.environ)
            except InvalidRevisionTest as error:
                raise pytest.UsageError(f"{item.nodeid}: {error}") from error
        retired = decided[item.path]
        if retired is not None:
            item.add_marker(pytest.mark.skip(reason=retired.reason))


def parse_shard(spec: str) -> tuple[int, int]:
    """``"2/4"`` -> ``(2, 4)``; anything but ``1 <= index <= count`` is a usage error."""
    index, sep, count = spec.partition("/")
    if sep and index.isdigit() and count.isdigit() and 1 <= int(index) <= int(count):
        return int(index), int(count)
    raise pytest.UsageError(f"{SHARD_ENV}={spec!r}: expected <index>/<count> with 1 <= index <= count")


def shard_of(nodeid: str, count: int) -> int:
    """The 1-based shard a test belongs to — a stable hash of its nodeid, identical in every
    process, so xdist workers agree on the collection and every test lands in exactly one shard."""
    return zlib.crc32(nodeid.encode()) % count + 1


# Identity vars a runner injects into worker spawn (``ClaudeCodeAdapter._spawn_env``);
# kept in sync by ``tests/test_runner_harness_adapter.py``.
_WORKER_IDENTITY_ENV = (
    "BLIZZARD_ENV_IDS",
    "BLIZZARD_ENV_WORKDIRS",
    "BLIZZARD_SESSION_ID",
    "BLIZZARD_CHUNK_ID",
    "BLIZZARD_LEASE_ID",
    "BLIZZARD_RUNNER_URL",
    "BLIZZARD_LEASE_TOKEN",
    "BLIZZARD_RUNNER_ASK_CMD",
    "BLIZZARD_ELICITATION",
    "BLIZZARD_TMPDIR",
)


@pytest.fixture(autouse=True)
def _strip_worker_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the worker identity vars so the suite is green inside a blizzard worker."""
    for name in _WORKER_IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _strip_hub_coordinates_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the hub URL and every runner token variable — the pre-host unsuffixed one included — so a developer's
    shell never leaks them into an in-process host config load."""
    monkeypatch.delenv(ENV_HUB_URL, raising=False)
    for name in list(os.environ):
        if name == HUB_TOKEN_VARIABLE_PREFIX.rstrip("_") or name.startswith(HUB_TOKEN_VARIABLE_PREFIX):
            monkeypatch.delenv(name, raising=False)


# What switches operator tracing on: a configured OTLP endpoint, absent a worker's own trace context.
_OPERATOR_TRACING_ENV = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_SDK_DISABLED",
    "OTEL_TRACES_EXPORTER",
    "BLIZZARD_TRACEPARENT",
)


@pytest.fixture(autouse=True)
def _isolated_operator_tracing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the OTLP and trace-context vars so a developer's shell never switches operator tracing on."""
    for name in _OPERATOR_TRACING_ENV:
        monkeypatch.delenv(name, raising=False)


def pytest_configure() -> None:
    """Unsign every git commit the suite spawns: parallel workers overload a host's gpg-agent.

    Also drop the legacy forge variables a feature-env shell carries: every hub the suite
    spawns inherits this environ, and a hub refuses to start while one is set.

    Switch the tenant and runner statement guards on for every engine the suite opens: in process here, and in every
    hub and runner subprocess, which inherits the variables and installs its guard as it opens its engine.

    Switch strict config reads on the same way: an undeclared key read through a daemon's config registry raises, in
    process and in every spawned daemon."""
    for name in LEGACY_FORGE_VARIABLES:
        os.environ.pop(name, None)
    os.environ.update(
        {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "commit.gpgsign",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "tag.gpgsign",
            "GIT_CONFIG_VALUE_1": "false",
        }
    )
    os.environ[GUARD_ENVIRONMENT_VARIABLE] = "1"
    install_tenant_guard()
    os.environ[RUNNER_GUARD_ENVIRONMENT_VARIABLE] = "1"
    install_runner_guard()
    os.environ[STRICT_CONFIG_ENVIRONMENT_VARIABLE] = "1"


@pytest.fixture(autouse=True)
def _isolated_session_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI's session-token file at a per-test temp dir.

    Without isolation, a real on-disk ``blizzard hub login`` token leaks into any test —
    the same ambient-host-state hazard :func:`_strip_worker_identity_env` closes for env vars.
    """
    monkeypatch.setattr(
        session_file.platformdirs, "user_config_dir", lambda _app: str(tmp_path / "config" / "blizzard")
    )
