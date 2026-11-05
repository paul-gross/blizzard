"""Suite-wide pytest wiring, registered by the root ``conftest.py`` so it reaches every collected directory:
the tier-marker refusal, store-revision retirement, sharding, the statement guards, and the autouse isolation fixtures."""

from __future__ import annotations

import fnmatch
import json
import os
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.operator_sessions.internal import session_file
from blizzard.hub.store.tenant_guard import install_tenant_guard
from blizzard.runner.store.runner_guard import install_runner_guard
from tests.support.head_replay_guard import install_head_replay_guard
from tests.support.repo_files import install_repo_read_guard, repo_root
from tests.support.revision_age import (
    InvalidRevisionTest,
    Retired,
    colocated_versions,
    decide_colocated,
)
from tests.support.root_test_modules import ROOT_TEST_MODULES
from tests.support.tier_layout import (
    declared_layout_marker,
    misplaced_root_module,
    non_package_folder,
    tier_of,
    unmirrored_folder,
)

# Install before test-module imports so collection-time reads are covered too.
install_repo_read_guard()
install_head_replay_guard()


#: The verification tiers declared in ``pyproject.toml``'s ``markers`` list.
_TIER_MARKERS = frozenset(
    {"unit", "component", "service", "crash_sweep", "rehearsal", "store_baseline", "winter_binding"}
)

#: ``<index>/<count>`` (1-based) keeps only that shard of the collected suite, so CI splits
#: one tier across parallel jobs; unset keeps everything.
SHARD_ENV = "BLIZZARD_TEST_SHARD"

#: The unit a shard is cut by, ``test`` (the default) or ``file``; ``docs/ci.md`` owns which tier uses which and why.
SHARD_BY_ENV = "BLIZZARD_TEST_SHARD_BY"
SHARD_BY_TEST = "test"
SHARD_BY_FILE = "file"


#: The two trees the folder layout governs: tests root, the package root its folders mirror, and the flat modules
#: still allowed directly under the root. The exemplar tree admits none.
LAYOUT_TREES: tuple[tuple[Path, Path, frozenset[str]], ...] = (
    (repo_root() / "tests", repo_root() / "src" / "blizzard", ROOT_TEST_MODULES),
    (repo_root() / "exemplars" / "python" / "tests", repo_root() / "exemplars" / "python" / "exemplar", frozenset()),
)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply the folder layout, refuse an unmarked test, then keep only this run's shard.

    The layout step runs first, so a folder's tier marker is on each item before pytest's ``-m``
    deselection and before the unmarked refusal.
    """
    apply_folder_layout(items)
    refuse_unmarked(items)
    _retire_aged_revision_tests(items)
    spec = os.environ.get(SHARD_ENV)
    if not spec or shard_by(os.environ) == SHARD_BY_FILE:
        return
    index, count = parse_shard(spec)
    deselected = [item for item in items if shard_of(item.nodeid, count) != index]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if shard_of(item.nodeid, count) == index]


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Skip a test module another file shard owns, before it is imported.

    Only under ``BLIZZARD_TEST_SHARD_BY=file``. ``None`` leaves every other path to pytest's own rules, so pytest's
    discovery still decides what a test module is.
    """
    spec = os.environ.get(SHARD_ENV)
    if not spec or shard_by(os.environ) != SHARD_BY_FILE:
        return None
    patterns: list[str] = config.getini("python_files")
    is_test_module = any(fnmatch.fnmatch(collection_path.name, pattern) for pattern in patterns)
    if not is_test_module or not collection_path.is_file() or not collection_path.is_relative_to(config.rootpath):
        return None
    index, count = parse_shard(spec)
    return True if shard_of(collection_path.relative_to(config.rootpath).as_posix(), count) != index else None


def apply_folder_layout(items: list[pytest.Item]) -> None:
    """Give each test under a tier folder that folder's tier; refuse a misplaced or self-marked file.

    Refusals are judged on the collected items, so a narrowed run judges only what it collects. A file's place is
    judged once, however many tests it holds.
    """
    file_tiers: dict[Path, tuple[str, ...]] = {}
    for item in items:
        if item.path not in file_tiers:
            file_tiers[item.path] = _tiers_of_file(item.path, item.nodeid)
        for tier in file_tiers[item.path]:
            declared = declared_layout_marker(mark.name for mark in item.iter_markers())
            if declared is not None:
                raise pytest.UsageError(
                    f"{item.nodeid}: declares the {declared!r} marker; its folder gives the tier, so drop the marker"
                )
            item.add_marker(getattr(pytest.mark, tier))


def _tiers_of_file(path: Path, nodeid: str) -> tuple[str, ...]:
    """The tier each layout tree's folders give ``path``; refuse it when it sits where no tier folder may.

    ``nodeid`` is the first collected test in the file, named by a refusal.
    """
    tiers: list[str] = []
    for tests_root, package_root, root_modules in LAYOUT_TREES:
        if misplaced_root_module(path, tests_root, root_modules):
            raise pytest.UsageError(
                f"{nodeid}: a flat test module under {tests_root.name}/; place it in unit/, component/ or guards/"
            )
        tier = tier_of(path, tests_root)
        if tier is None:
            continue
        if (folder := non_package_folder(path, tests_root)) is not None:
            raise pytest.UsageError(f"{nodeid}: {folder} has no __init__.py; every tier folder is a package")
        if (folder := unmirrored_folder(path, tests_root, package_root)) is not None:
            raise pytest.UsageError(f"{nodeid}: {folder} mirrors no package under {package_root.name}/")
        tiers.append(tier)
    return tuple(tiers)


def refuse_unmarked(items: list[pytest.Item]) -> None:
    """Refuse the run when a collected test carries none of the tier markers.

    An unmarked test still runs in the default suite but is invisible to both ``-m unit`` and ``-m component``. Judged
    on what this run collects, before any test split, so every shard together judges the whole suite.
    """
    unmarked = [item.nodeid for item in items if not (_TIER_MARKERS & {mark.name for mark in item.iter_markers()})]
    if unmarked:
        raise pytest.UsageError(
            f"these collected tests carry none of {', '.join(sorted(_TIER_MARKERS))}: {', '.join(unmarked)}"
        )


def _retire_aged_revision_tests(items: list[pytest.Item]) -> None:
    """Skip each colocated store-migration test whose revision has aged out; refuse a file naming no sibling revision.

    Judged per file before the test split, so a bad name fails whichever shard collects the file, and a shard's
    membership stays a hash of the nodeid or path alone.
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


def shard_by(environ: Mapping[str, str]) -> str:
    """The unit ``BLIZZARD_TEST_SHARD_BY`` names, ``test`` when unset; any other value is a usage error."""
    unit = environ.get(SHARD_BY_ENV) or SHARD_BY_TEST
    if unit not in (SHARD_BY_TEST, SHARD_BY_FILE):
        raise pytest.UsageError(f"{SHARD_BY_ENV}={unit!r}: expected {SHARD_BY_TEST!r} or {SHARD_BY_FILE!r}")
    return unit


def shard_of(key: str, count: int) -> int:
    """The 1-based shard a test or test module belongs to — a stable hash of its nodeid or repo-relative path,
    identical in every process, so xdist workers agree on the collection and each lands in exactly one shard."""
    return zlib.crc32(key.encode()) % count + 1


#: The environment every daemon the suite spawns inherits, declared once so the Playwright harness reads the same file.
DAEMON_ENV_PATH = Path(__file__).resolve().parent.parent / "daemon-env.json"
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
    """Unset what the contract unsets — worker identity (kept in sync with ``ClaudeCodeAdapter.spawn_env`` by
    ``tests/unit/runner/harness/claude_code/test_adapter.py``), hub coordinates, runner tokens, tracing — so a
    developer's shell or a blizzard worker's never leaks into an in-process config load."""
    for name in _contract_names_present():
        monkeypatch.delenv(name, raising=False)


def _without_opencode(path: str) -> str:
    """``path`` less every directory holding an ``opencode`` executable: the binary's version chooses the binding a
    runner composes, so a developer's own OpenCode never decides which one a test gets."""
    kept = [entry for entry in path.split(os.pathsep) if not os.access(Path(entry, "opencode"), os.X_OK)]
    return os.pathsep.join(kept)


def pytest_configure() -> None:
    """Apply the daemon environment contract to the suite's environ: every hub and runner subprocess inherits it.

    That unsigns every git commit the suite spawns (parallel workers overload a host's gpg-agent), drops the legacy
    forge variables a hub refuses to start with, and switches the statement guards and strict config reads on; the
    guards also install in process here, as each daemon installs its own as it opens its engine."""
    for name in DAEMON_ENV["unset"]:
        os.environ.pop(name, None)
    os.environ.update(DAEMON_ENV["set"])
    os.environ["PATH"] = _without_opencode(os.environ.get("PATH", ""))
    install_tenant_guard()
    install_runner_guard()


@pytest.fixture(autouse=True)
def _isolated_session_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI's session-token file at a per-test temp dir.

    Without isolation, a real on-disk ``blizzard hub login`` token leaks into any test —
    the same ambient-host-state hazard :func:`_strip_daemon_env` closes for env vars.
    """

    def user_config_dir(appname: str | None = None) -> str:
        return str(tmp_path / "config" / "blizzard")

    monkeypatch.setattr(session_file.platformdirs, "user_config_dir", user_config_dir)
