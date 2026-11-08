"""Own SQLAlchemy engines created by a pytest session at their creating scope.

The constructor boundary covers imported factory aliases as well as application builders.
A scope retains engines even after an explicit dispose, since they can open a new pool.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, Thread, local
from typing import Any, cast

import pytest
from _pytest.config import Config
from _pytest.fixtures import FixtureDef, SubRequest
from _pytest.nodes import Node
from _pytest.reports import TestReport
from sqlalchemy.engine import Engine

from tests.support import open_file_guard

_lock = RLock()
_local = local()
_sessions: list[EngineOwner] = []
_original_init = Engine.__init__
_original_thread_start = Thread.start
_original_submit = ThreadPoolExecutor.submit
_original_configure = cast(Callable[[Config], None], Config._do_configure)  # pyright: ignore[reportPrivateUsage]
_original_finish = cast(Callable[[FixtureDef[Any], SubRequest], None], FixtureDef.finish)  # pyright: ignore[reportUnknownMemberType]


class EngineOwner:
    """A session's engine sets, keyed by their pytest scope node."""

    def __init__(self, session: pytest.Session) -> None:
        self.session = session
        self.engines: dict[Node, list[Engine]] = {session: []}
        self.engine_paths: dict[Engine, set[Path]] = {}
        self.current: Node = session
        self.session_finalizer_registered = False
        self.temp_finalizers: dict[Node, list[Callable[[], None]]] = {}
        self.temp_paths: dict[Node, Path] = {}
        self.deferred_paths: dict[Node, set[Path]] = {}

    def track(self, engine: Engine) -> None:
        with _lock:
            node = getattr(_local, "node", None)
            if node is None or node.session is not self.session:
                node = self.current
            # Collection precedes pytest's setup stack, so the session's
            # finalizer is registered when its first fixture starts instead.
            if node is not self.session:
                self.ensure(node)
            self.engines[node].append(engine)
            self.engine_paths[engine] = _sqlite_paths([engine])

    def ensure(self, node: Node) -> None:
        with _lock:
            if node is self.session and not self.session_finalizer_registered:
                node.addfinalizer(lambda: self.dispose(node))
                self.session_finalizer_registered = True
            if node not in self.engines:
                self.engines[node] = []
                node.addfinalizer(lambda: self.dispose(node))

    def dispose(self, node: Node) -> None:
        with _lock:
            engines = self.engines.pop(node, [])
        errors: list[BaseException] = []
        for engine in reversed(engines):
            try:
                engine.dispose()
            except BaseException as error:
                errors.append(error)
        paths = self.paths_for(engines) | self.deferred_paths.pop(node, set())
        for engine in engines:
            self.engine_paths.pop(engine, None)
        # A descriptor cannot be attributed to one engine when a wider scope
        # has another engine on the same file. Carry the obligation forward;
        # the last owner must prove *all* handles to that path are closed.
        with _lock:
            for owner in _sessions:
                for other, other_engines in owner.engines.items():
                    shared = paths & (owner.paths_for(other_engines) | owner.deferred_paths.get(other, set()))
                    if shared:
                        owner.deferred_paths.setdefault(other, set()).update(shared)
                        paths -= shared
        root = self.temp_paths.pop(node, None)
        if root is not None or paths:
            try:
                findings = open_file_guard.inspect(root, paths)
                if findings:
                    scope_name = node.nodeid or f"<{type(node).__name__}:{node.name}>"
                    errors.append(AssertionError(open_file_guard.describe(scope_name, findings)))
            except BaseException as error:
                errors.append(error)
        for finalizer in self.temp_finalizers.pop(node, []):
            try:
                finalizer()
            except BaseException as error:
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("engine disposal failed", errors)

    def paths_for(self, engines: list[Engine]) -> set[Path]:
        paths: set[Path] = set()
        for engine in engines:
            if engine in self.engine_paths:
                paths.update(self.engine_paths[engine])
            else:
                paths.update(_sqlite_paths([engine]))
        return paths


def _sqlite_paths(engines: list[Engine]) -> set[Path]:
    databases = {
        Path(engine.url.database).resolve()
        for engine in engines
        if engine.url.get_backend_name() == "sqlite" and engine.url.database and engine.url.database != ":memory:"
    }
    return databases | {Path(f"{database}{suffix}") for database in databases for suffix in ("-wal", "-shm")}


def _engine_init(self: Engine, *args: Any, **kwargs: Any) -> None:
    _original_init(self, *args, **kwargs)
    with _lock:
        if _sessions and not getattr(_local, "prototype", False):
            owner = getattr(_local, "owner", None)
            (owner if owner in _sessions else _sessions[-1]).track(self)


def _thread_start(self: Thread) -> None:
    with _lock:
        owner = getattr(_local, "owner", None)
        if owner not in _sessions:
            owner = _sessions[-1] if _sessions else None
        node = getattr(_local, "node", None)
        if owner is not None and (node is None or node.session is not owner.session):
            node = owner.current
        is_prototype = getattr(_local, "prototype", False)
    run = self.run

    def run_in_scope() -> None:
        _local.owner = owner
        _local.node = node
        _local.prototype = is_prototype
        try:
            run()
        finally:
            del _local.owner, _local.node, _local.prototype

    self.run = run_in_scope
    _original_thread_start(self)


def _submit(self: ThreadPoolExecutor, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    owner = getattr(_local, "owner", None)
    node = getattr(_local, "node", None)
    is_prototype = getattr(_local, "prototype", False)

    def run_in_scope() -> Any:
        previous_owner = getattr(_local, "owner", None)
        previous_node = getattr(_local, "node", None)
        previous_prototype = getattr(_local, "prototype", False)
        _local.owner, _local.node, _local.prototype = owner, node, is_prototype
        try:
            return fn(*args, **kwargs)
        finally:
            _local.owner, _local.node, _local.prototype = previous_owner, previous_node, previous_prototype

    return _original_submit(self, run_in_scope)


def start(session: pytest.Session) -> EngineOwner:
    with _lock:
        if not _sessions:
            Engine.__init__ = _engine_init
            Thread.start = _thread_start
            ThreadPoolExecutor.submit = _submit
            Config._do_configure = _configure  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
            FixtureDef.finish = _fixture_finish  # pyright: ignore[reportAttributeAccessIssue]
        owner = EngineOwner(session)
        _sessions.append(owner)
        return owner


def finish(owner: EngineOwner) -> None:
    try:
        errors: list[BaseException] = []
        for node in reversed(list(owner.engines)):
            try:
                owner.dispose(node)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("session engine disposal failed", errors)
    finally:
        with _lock:
            _sessions.remove(owner)
            if not _sessions:
                Engine.__init__ = _original_init
                Thread.start = _original_thread_start
                ThreadPoolExecutor.submit = _original_submit
                Config._do_configure = _original_configure  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
                FixtureDef.finish = _original_finish  # pyright: ignore[reportAttributeAccessIssue]


@contextmanager
def scope(owner: EngineOwner, node: Node) -> Generator[None]:
    previous = getattr(_local, "node", None)
    previous_owner = getattr(_local, "owner", None)
    previous_current = owner.current
    _local.node = node
    _local.owner = owner
    owner.current = node
    try:
        yield
    finally:
        _local.node = previous
        _local.owner = previous_owner
        owner.current = previous_current


@contextmanager
def prototype() -> Generator[None]:
    """Leave the two intentional process-lifetime migration caches alone."""
    previous = getattr(_local, "prototype", False)
    _local.prototype = True
    try:
        yield
    finally:
        _local.prototype = previous


_OWNER = pytest.StashKey[EngineOwner]()


class LifecyclePlugin:
    """Install the owner in one pytest config, including nested in-process runs."""

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        owner = start(session)
        session.config.stash[_OWNER] = owner
        self.previous_owner = getattr(_local, "owner", None)
        self.previous_node = getattr(_local, "node", None)
        _local.owner = owner
        _local.node = session

    def __init__(self) -> None:
        self.previous_owner: EngineOwner | None = None
        self.previous_node: Node | None = None
        self.workers: set[str] = set()
        self.results: dict[str, str] = {}
        self.last_teardown: tuple[str, str] | None = None
        self.last_scan: tuple[str, int | None, str] | None = None
        self.teardown_counts: dict[str, int] = {}
        self.teardown_count = 0

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item: pytest.Item) -> Generator[Any, Any, Any]:
        try:
            return (yield)
        finally:
            # xdist can assign more work dynamically, so retain each scan and judge the last one at session finish.
            self.last_scan = None
            factory = getattr(item.config, "_tmp_path_factory", None)
            if factory is not None:
                findings = open_file_guard.inspect(factory.getbasetemp())
                count = len(findings) if sys.platform.startswith("linux") else None
                self.last_scan = (item.nodeid, count, open_file_guard.describe("last-teardown", findings))

    def pytest_runtest_logreport(self, report: TestReport) -> None:
        if report.when == "teardown":
            self.last_teardown = (report.nodeid, report.outcome)
            self.teardown_count += 1
            worker = getattr(report, "worker_id", None)
            if worker is not None:
                self.teardown_counts[worker] = self.teardown_counts.get(worker, 0) + 1

    @pytest.hookimpl(wrapper=True, trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> Generator[Any, Any, Any]:
        result = yield
        try:
            finish(session.config.stash[_OWNER])
        finally:
            _local.owner = self.previous_owner
            _local.node = self.previous_node
        config = session.config
        if not hasattr(config, "workerinput") and getattr(config.option, "numprocesses", None):
            missing = self.workers - self.results.keys()
            if missing or any(self.results.values()):
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
            if missing:
                terminal = config.pluginmanager.get_plugin("terminalreporter")
                if terminal is not None:
                    terminal.write_line(f"open-file guard: missing worker evidence: {sorted(missing)}")
            return result
        factory = getattr(config, "_tmp_path_factory", None)
        if factory is not None:
            findings = open_file_guard.inspect(factory.getbasetemp())
            message = open_file_guard.describe("terminal", findings)
            workeroutput = cast(dict[str, object] | None, getattr(config, "workeroutput", None))
            if workeroutput is not None:
                workeroutput["open_file_guard"] = message
                workeroutput["open_file_guard_count"] = len(findings) if sys.platform.startswith("linux") else None
                workeroutput["open_file_guard_last_teardown"] = self.last_teardown
                workeroutput["open_file_guard_last_scan"] = self.last_scan
                workeroutput["open_file_guard_teardown_count"] = self.teardown_count
            scan_clean = (
                self.last_scan is not None
                and self.last_teardown is not None
                and self.last_scan[0] == self.last_teardown[0]
                and (self.last_scan[1] == 0 if sys.platform.startswith("linux") else self.last_scan[1] is None)
            )
            if (
                findings
                or (self.last_teardown is not None and not scan_clean)
                or (
                    workeroutput is not None
                    and self.teardown_count > 0
                    and (not scan_clean or self.last_teardown is None or self.last_teardown[1] != "passed")
                )
            ):
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
                if workeroutput is not None:
                    workeroutput["exitstatus"] = int(session.exitstatus)
            terminal = config.pluginmanager.get_plugin("terminalreporter")
            if terminal is not None:
                if self.last_scan is not None:
                    terminal.write_line(self.last_scan[2])
                terminal.write_line(message)
        return result

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node: Any) -> None:
        self.workers.add(node.gateway.id)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: Any) -> None:
        output = getattr(node, "workeroutput", {})
        message = output.get("open_file_guard")
        if message is not None:
            last = output.get("open_file_guard_last_teardown")
            scan = output.get("open_file_guard_last_scan")
            expected = 0 if sys.platform.startswith("linux") else None
            terminal_clean = "open_file_guard_count" in output and output["open_file_guard_count"] == expected
            scan_clean = scan is not None and last is not None and scan[0] == last[0] and scan[1] == expected
            count = output.get("open_file_guard_teardown_count")
            count_valid = type(count) is int and count == self.teardown_counts.get(node.gateway.id, 0)
            test_clean = (count == 0 and last is None and scan is None) or (
                count_valid and scan_clean and last is not None and last[1] == "passed"
            )
            self.results[node.gateway.id] = (
                "" if terminal_clean and count_valid and test_clean and error is None else message
            )
            terminal = node.config.pluginmanager.get_plugin("terminalreporter")
            if terminal is not None:
                terminal.write_line(message)
                if "open_file_guard_count" not in output or (
                    sys.platform.startswith("linux") and output["open_file_guard_count"] is None
                ):
                    terminal.write_line(f"open-file guard: worker={node.gateway.id} missing terminal scan evidence")
                if not count_valid:
                    terminal.write_line(f"open-file guard: worker={node.gateway.id} missing or inconsistent test count")
                if count != 0 and (scan is None or (sys.platform.startswith("linux") and scan[1] is None)):
                    terminal.write_line(
                        f"open-file guard: worker={node.gateway.id} missing last-teardown scan evidence"
                    )
                elif scan is not None:
                    terminal.write_line(scan[2])
                if count != 0 and last is None:
                    terminal.write_line(
                        f"open-file guard: worker={node.gateway.id} missing last-test teardown evidence"
                    )
                elif last is not None:
                    terminal.write_line(
                        f"open-file guard: worker={node.gateway.id} last-test={last[0]} teardown={last[1]}"
                    )

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef: FixtureDef[Any], request: SubRequest) -> Generator[Any, Any, Any]:
        owner = request.config.stash[_OWNER]
        # Reserve the node before any fixture finalizer can create its first engine.
        owner.ensure(request.node)
        with scope(owner, request.node):
            result = yield
        if fixturedef.argname == "tmp_path" and fixturedef.func.__module__ == "_pytest.tmpdir":
            owner.temp_paths[request.node] = result
        return result

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[Any, Any, Any]:
        with scope(item.config.stash[_OWNER], item):
            return (yield)


def install(config: pytest.Config) -> None:
    if not config.pluginmanager.hasplugin("engine-lifecycle"):
        config.pluginmanager.register(LifecyclePlugin(), "engine-lifecycle")


def pytest_configure(config: pytest.Config) -> None:
    """Install ownership when xdist starts a nested session in a fresh interpreter."""
    install(config)


def _configure(config: Config) -> None:
    # A nested pytest.main need not load the suite's conftest or plugin.
    install(config)
    if getattr(config.option, "numprocesses", None):
        params = config.invocation_params
        config.invocation_params = Config.InvocationParams(
            args=(*params.args, "-p", "tests.support.engine_lifecycle"), plugins=params.plugins, dir=params.dir
        )
    _original_configure(config)


def _fixture_finish(fixturedef: FixtureDef[Any], request: SubRequest) -> None:
    owner = request.config.stash.get(_OWNER, None)
    if owner is None:
        _original_finish(fixturedef, request)
        return
    if fixturedef.argname == "tmp_path" and fixturedef.func.__module__ == "_pytest.tmpdir":
        # Pytest may delete tmp_path in its own fixture finalizer. Defer that
        # housekeeping until every fixture finalizer and engine disposal has run.
        finalizers = owner.temp_finalizers.setdefault(request.node, [])
        if not finalizers:
            finalizers.append(lambda: _original_finish(fixturedef, request))
        return
    with scope(owner, request.node):
        _original_finish(fixturedef, request)


def ensure_test_scope(request: SubRequest, tmp_path: Path) -> None:
    owner = request.config.stash[_OWNER]
    owner.ensure(request.node)
    owner.temp_paths[request.node] = tmp_path
