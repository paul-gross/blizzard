"""Suite-wide pytest wiring, registered by the root ``conftest.py`` so it reaches every collected directory:
the tier-marker refusal, store-revision retirement, sharding, the statement guards, and the autouse isolation fixtures."""

from __future__ import annotations

import fnmatch
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import _pytest.tmpdir as pytest_tmpdir
import pytest
from _pytest.fixtures import SubRequest

from blizzard.foundation.operator_sessions.internal import session_file
from blizzard.hub.store.tenant_guard import install_tenant_guard
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.store.runner_guard import install_runner_guard
from tests.support import engine_lifecycle
from tests.support.head_replay_guard import install_head_replay_guard
from tests.support.repo_files import install_repo_read_guard, repo_root
from tests.support.revision_age import (
    InvalidRevisionTest,
    Retired,
    colocated_versions,
    decide_colocated,
)
from tests.support.shard_weights import (
    SUITES,
    TIMINGS_ENV,
    Inventory,
    SuiteWeights,
    TimingRecorder,
    hash_shard,
    load_inventory,
    place,
    unit_weight,
)
from tests.support.shard_weights import pytest_file_unit as file_unit_of
from tests.support.tier_layout import (
    declared_layout_marker,
    flat_test_module,
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


@pytest.fixture
def short_host_runtime() -> Iterator[Path]:
    """A private, short-lived directory for test host sockets."""
    with tempfile.TemporaryDirectory(prefix="s-") as runtime:
        yield Path(runtime)


@pytest.fixture
def short_tmp_path(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep real host paths short, including in subprocesses, without replacing pytest's tmp_path finalizer."""
    original = pytest_tmpdir._mk_tmp  # pyright: ignore[reportPrivateUsage]
    node: object = request.node  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # pytest leaves node untyped
    with tempfile.TemporaryDirectory(prefix="t-") as directory:

        def make_tmp(inner: pytest.FixtureRequest, factory: pytest.TempPathFactory) -> Path:
            inner_node: object = inner.node  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # pytest leaves node untyped
            if inner_node is node:
                return Path(directory)
            return original(inner, factory)

        monkeypatch.setattr(pytest_tmpdir, "_mk_tmp", make_tmp)
        yield


@pytest.fixture
def short_host_sockets(monkeypatch: pytest.MonkeyPatch, short_host_runtime: Path) -> None:
    """Keep each host's sockets short while its store and other files stay in the test directory."""
    roots: dict[Path, Path] = {}

    def socket_for(layout: HostLayout, name: str) -> Path:
        root = layout.root.resolve()
        if root not in roots:
            directory = short_host_runtime / str(len(roots))
            directory.mkdir()
            roots[root] = directory
        return roots[root] / name

    monkeypatch.setattr(HostLayout, "socket_path", property(lambda layout: socket_for(layout, "host.sock")))
    monkeypatch.setattr(HostLayout, "legacy_socket_path", property(lambda layout: socket_for(layout, "runner.sock")))


#: The two trees the folder layout governs: each tests root and the package root its folders mirror.
LAYOUT_TREES: tuple[tuple[Path, Path], ...] = (
    (repo_root() / "tests", repo_root() / "src" / "blizzard"),
    (repo_root() / "exemplars" / "python" / "tests", repo_root() / "exemplars" / "python" / "exemplar"),
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
    inventory = _inventory(config)
    nodeids = [item.nodeid for item in items]
    weights = {nodeid: unit_weight(inventory, _TEST_SUITE, [nodeid]) for nodeid in nodeids}
    owned = set(place(weights, count)[index - 1])
    config.stash[_UNMEASURED] = _unmeasured(inventory, _TEST_SUITE, owned, nodeids)
    deselected = [item for item in items if item.nodeid not in owned]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if item.nodeid in owned]


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Skip a test module another file shard owns, before it is imported.

    Only under ``BLIZZARD_TEST_SHARD_BY=file``. ``None`` leaves every other path to pytest's own rules, so pytest's
    discovery still decides what a test module is. A module the walk of the run's collection roots found is owned by
    the weighted placement; any other keeps its stable-hash owner.
    """
    spec = os.environ.get(SHARD_ENV)
    if not spec or shard_by(os.environ) != SHARD_BY_FILE:
        return None
    if not _is_test_module(collection_path, config) or not collection_path.is_file():
        return None
    if not collection_path.is_relative_to(config.rootpath):
        return None
    index, count = parse_shard(spec)
    unit = collection_path.relative_to(config.rootpath).as_posix()
    owner = _file_owners(config, count).get(unit) or shard_of(unit, count)
    return True if owner != index else None


#: The inventory suite each pytest pool weighs its units by.
_FILE_SUITE = next(entry.id for entry in SUITES if entry.kind == "cases" and entry.pool == "file")
_TEST_SUITE = next(entry.id for entry in SUITES if entry.kind == "cases" and entry.pool == "test")

#: The inventory a session places by, loaded once per process.
SHARD_INVENTORY = pytest.StashKey[Inventory]()
_FILE_OWNERS = pytest.StashKey[dict[str, int]]()
_UNMEASURED = pytest.StashKey[list[str]]()


def _inventory(config: pytest.Config) -> Inventory:
    if SHARD_INVENTORY not in config.stash:
        config.stash[SHARD_INVENTORY] = load_inventory()
    return config.stash[SHARD_INVENTORY]


def _is_test_module(path: Path, config: pytest.Config) -> bool:
    patterns: list[str] = config.getini("python_files")
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def collection_modules(config: pytest.Config) -> list[str]:
    """Every test module under the run's collection roots, repo-relative and sorted — a walk by path and
    ``python_files`` alone, so every shard and xdist worker of one command finds the same list before importing any."""
    skip: list[str] = config.getini("norecursedirs")
    found: set[str] = set()
    for arg in config.args:
        root = Path(arg.partition("::")[0])
        root = root if root.is_absolute() else config.invocation_params.dir / root
        candidates = [root] if root.is_file() else []
        for directory, folders, files in os.walk(root):
            folders[:] = [name for name in folders if not any(fnmatch.fnmatch(name, glob) for glob in skip)]
            candidates += [Path(directory, name) for name in files]
        for path in candidates:
            path = path.resolve()
            if _is_test_module(path, config) and path.is_relative_to(config.rootpath):
                found.add(path.relative_to(config.rootpath).as_posix())
    return sorted(found)


def file_weights(inventory: Inventory, modules: list[str]) -> dict[str, int]:
    """Each module's weight: the sum of its inventoried cases, or the file pool's fallback when it has none."""
    weights = inventory.get(_FILE_SUITE, SuiteWeights())
    cases: dict[str, list[str]] = {module: [] for module in modules}
    for nodeid in weights.entries:
        if (module := file_unit_of(nodeid)) in cases:
            cases[module].append(nodeid)
    return {
        module: unit_weight(inventory, _FILE_SUITE, members) if members else weights.fallback
        for module, members in cases.items()
    }


def _file_owners(config: pytest.Config, count: int) -> dict[str, int]:
    """The 1-based owning shard of each walked module, computed once per process."""
    if _FILE_OWNERS not in config.stash:
        inventory = _inventory(config)
        modules = collection_modules(config)
        placed = place(file_weights(inventory, modules), count)
        config.stash[_FILE_OWNERS] = {unit: shard for shard, units in enumerate(placed, 1) for unit in units}
        index, _ = parse_shard(os.environ[SHARD_ENV])
        config.stash[_UNMEASURED] = _unmeasured(inventory, _FILE_SUITE, set(placed[index - 1]), modules, by_file=True)
    return config.stash[_FILE_OWNERS]


def _unmeasured(
    inventory: Inventory, suite_id: str, owned: set[str], units: list[str], *, by_file: bool = False
) -> list[str]:
    """The units this shard owns that the inventory holds no measurement for."""
    weights = inventory.get(suite_id)
    entries = weights.entries if weights is not None else {}
    measured = {file_unit_of(nodeid) for nodeid in entries} if by_file else set(entries)
    return [unit for unit in units if unit in owned and unit not in measured]


class _WorkerNode(Protocol):
    """The slice of xdist's worker controller the collection hook reads."""

    config: pytest.Config


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: _WorkerNode, ids: list[str]) -> None:
    """Under xdist the controller collects nothing itself; a worker's shard collection names its unmeasured tests."""
    config = node.config
    spec = os.environ.get(SHARD_ENV)
    if not spec or _UNMEASURED in config.stash:
        return
    if shard_by(os.environ) == SHARD_BY_FILE:
        _file_owners(config, parse_shard(spec)[1])
        return
    entries = _inventory(config).get(_TEST_SUITE)
    measured = set(entries.entries) if entries is not None else set[str]()
    config.stash[_UNMEASURED] = [nodeid for nodeid in ids if nodeid not in measured]


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    """Name each unit this shard owns that weighed its suite's fallback, so a stale inventory shows in the job log."""
    unmeasured = config.stash.get(_UNMEASURED, None)
    if not unmeasured:
        return
    terminalreporter.section(f"shard placement: {len(unmeasured)} unmeasured unit(s) weighed the fallback")
    for unit in unmeasured:
        terminalreporter.write_line(unit)


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
    for tests_root, package_root in LAYOUT_TREES:
        if flat_test_module(path, tests_root):
            raise pytest.UsageError(
                f"{nodeid}: a test module directly under {tests_root.name}/, where bzh:mirrored-test-layout places none;"
                " move it to the folder its tier's rule names"
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
    membership never depends on it.
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
    """The stable-hash owner of a test module the collection-root walk did not find."""
    return hash_shard(key, count)


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


def pytest_configure(config: pytest.Config) -> None:
    """Apply the daemon environment contract to the suite's environ: every hub and runner subprocess inherits it.

    That unsigns every git commit the suite spawns (parallel workers overload a host's gpg-agent), drops the legacy
    forge variables a hub refuses to start with, and switches the statement guards and strict config reads on; the
    guards also install in process here, as each daemon installs its own as it opens its engine."""
    for name in DAEMON_ENV["unset"]:
        os.environ.pop(name, None)
    os.environ.update(DAEMON_ENV["set"])
    os.environ["PATH"] = _without_opencode(os.environ.get("PATH", ""))
    engine_lifecycle.install(config)
    if (timings := os.environ.get(TIMINGS_ENV)) and not hasattr(config, "workerinput"):
        config.pluginmanager.register(_TimingPlugin(TimingRecorder(Path(timings))), "blizzard-timings")
    install_tenant_guard()
    install_runner_guard()


class _TimingPlugin:
    """Under ``BLIZZARD_TEST_TIMINGS``, the controller (never an xdist worker) records each run nodeid's summed
    setup, call, and teardown seconds — xdist relays every worker's reports to it — and writes them at session end."""

    def __init__(self, recorder: TimingRecorder) -> None:
        self._recorder = recorder

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self._recorder.record(report.nodeid, report.duration)

    def pytest_sessionfinish(self) -> None:
        self._recorder.write()


@pytest.fixture(autouse=True)
def _isolated_session_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: SubRequest) -> None:
    """Point the CLI's session-token file at a per-test temp dir.

    Without isolation, a real on-disk ``blizzard hub login`` token leaks into any test —
    the same ambient-host-state hazard :func:`_strip_daemon_env` closes for env vars.
    """

    # Keep the isolation fixture in the tracked test scope; the owner defers pytest's tmp_path housekeeping.
    engine_lifecycle.ensure_test_scope(request, tmp_path)

    def user_config_dir(appname: str | None = None) -> str:
        return str(tmp_path / "config" / "blizzard")

    monkeypatch.setattr(session_file.platformdirs, "user_config_dir", user_config_dir)
