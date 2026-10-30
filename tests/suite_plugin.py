"""Suite-wide pytest wiring, registered by the root ``conftest.py`` so it reaches every collected directory:
the tier-marker record, store-revision retirement, sharding, the statement guards, and the autouse isolation fixtures."""

from __future__ import annotations

import json
import os
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.operator_sessions.internal import session_file
from blizzard.hub.store.tenant_guard import install_tenant_guard
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


#: The verification tiers declared in ``pyproject.toml``'s ``markers`` list.
_TIER_MARKERS = frozenset(
    {"unit", "component", "service", "e2e", "crash_sweep", "rehearsal", "store_baseline", "winter_binding"}
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


#: The environment every daemon the suite spawns inherits, declared once so the Playwright harness reads the same file.
DAEMON_ENV_PATH = Path(__file__).with_name("daemon-env.json")
DAEMON_ENV = json.loads(DAEMON_ENV_PATH.read_text())


def _contract_names_present() -> list[str]:
    """Every environ name the contract unsets, by exact name or prefix, as the environ stands now."""
    prefixes = tuple(DAEMON_ENV["unsetPrefixes"])
    exact = set(DAEMON_ENV["unset"])
    # A prefix ending in ``_`` also names the bare, unsuffixed variable (``BZ_HUB_TOKEN_`` -> ``BZ_HUB_TOKEN``).
    bare = {prefix.rstrip("_") for prefix in prefixes}
    return [name for name in os.environ if name in exact or name in bare or name.startswith(prefixes)]


@pytest.fixture(autouse=True)
def _strip_daemon_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset what the contract unsets — worker identity (kept in sync with ``ClaudeCodeAdapter._spawn_env`` by
    ``tests/test_runner_harness_adapter.py``), hub coordinates, runner tokens, tracing — so a developer's shell or a
    blizzard worker's never leaks into an in-process config load."""
    for name in _contract_names_present():
        monkeypatch.delenv(name, raising=False)


def pytest_configure() -> None:
    """Apply the daemon environment contract to the suite's environ: every hub and runner subprocess inherits it.

    That unsigns every git commit the suite spawns (parallel workers overload a host's gpg-agent), drops the legacy
    forge variables a hub refuses to start with, and switches the statement guards and strict config reads on; the
    guards also install in process here, as each daemon installs its own as it opens its engine."""
    for name in DAEMON_ENV["unset"]:
        os.environ.pop(name, None)
    os.environ.update(DAEMON_ENV["set"])
    install_tenant_guard()
    install_runner_guard()


@pytest.fixture(autouse=True)
def _isolated_session_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI's session-token file at a per-test temp dir.

    Without isolation, a real on-disk ``blizzard hub login`` token leaks into any test —
    the same ambient-host-state hazard :func:`_strip_daemon_env` closes for env vars.
    """
    monkeypatch.setattr(
        session_file.platformdirs, "user_config_dir", lambda _app: str(tmp_path / "config" / "blizzard")
    )
