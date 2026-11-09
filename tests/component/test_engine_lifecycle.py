"""Exercise suite engine ownership through actual, nested pytest sessions."""

import sys
from pathlib import Path
from types import ModuleType

import pytest
from _pytest.fixtures import SubRequest
from sqlalchemy import create_engine

from tests.support.engine_lifecycle import EngineOwner, finish, start


def test_relative_database_leak_is_inspected_after_cwd_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    module = tmp_path / "test_relative.py"
    module.write_text("""\
import os
from pathlib import Path
import lifecycle_relative_state as state
from sqlalchemy import create_engine

def test_relative():
    old = os.getcwd()
    try:
        os.chdir(Path(__file__).parent)
        engine = create_engine('sqlite:///relative.db')
        state.connection = engine.connect()
        state.connection.exec_driver_sql('select 1')
    finally:
        os.chdir(old)
""")
    state = ModuleType("lifecycle_relative_state")
    monkeypatch.setitem(sys.modules, state.__name__, state)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    try:
        result = pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"])
        assert result == pytest.ExitCode.TESTS_FAILED
    finally:
        state.connection.close()


def test_reused_executor_attributes_each_job_to_submitting_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    module = tmp_path / "test_executor.py"
    module.write_text("""\
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from sqlalchemy import create_engine

root = Path(__file__).parent
@pytest.fixture(scope='session')
def executor():
    with ThreadPoolExecutor(max_workers=1) as pool:
        yield pool

def create(name):
    engine = create_engine(f"sqlite:///{root / name}")
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')

def opened(name):
    return any(p.exists() and os.path.realpath(p) == str(root / name) for p in Path('/proc/self/fd').iterdir())

def test_a(executor):
    executor.submit(create, 'a.db').result()
    assert opened('a.db')

def test_b(executor):
    assert not opened('a.db')
    executor.submit(create, 'b.db').result()
    assert opened('b.db')

def test_c(executor):
    assert not opened('b.db')
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"]) == pytest.ExitCode.OK


def test_nested_session_can_share_outer_sqlite_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    path = tmp_path / "shared.db"
    outer = create_engine(f"sqlite:///{path}")
    with outer.connect() as connection:
        connection.exec_driver_sql("select 1")
    module = tmp_path / "test_shared.py"
    module.write_text("""\
from pathlib import Path
from sqlalchemy import create_engine

def test_shared():
    engine = create_engine(f"sqlite:///{Path(__file__).parent / 'shared.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"]) == pytest.ExitCode.OK
    with outer.connect() as connection:
        connection.exec_driver_sql("select 1")


def test_collection_engine_lives_until_nested_session_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    module = tmp_path / "test_collection.py"
    module.write_text("""\
import os
from pathlib import Path
from sqlalchemy import create_engine

path = Path(__file__).parent / 'collection.db'
engine = create_engine(f'sqlite:///{path}')
with engine.connect() as connection:
    connection.exec_driver_sql('select 1')

def opened():
    return any(p.exists() and os.path.realpath(p) == str(path) for p in Path('/proc/self/fd').iterdir())

def test_first():
    assert opened()

def test_second():
    assert opened()
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"]) == pytest.ExitCode.OK
    path = tmp_path / "collection.db"
    assert not any(p.exists() and p.resolve() == path for p in Path("/proc/self/fd").iterdir())


def test_isolated_session_store_finalizer_precedes_engine_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    module = tmp_path / "test_isolation.py"
    module.write_text("""\
import os
from pathlib import Path
import pytest
from sqlalchemy import create_engine

pytestmark = pytest.mark.unit
root = Path(__file__).parent
path = root / 'isolation-finalizer.db'

@pytest.fixture
def after_isolation(_isolated_session_store):
    yield
    engine = create_engine(f'sqlite:///{path}')
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')

def test_first(after_isolation):
    pass

def test_second():
    assert not any(p.exists() and os.path.realpath(p) == str(path) for p in Path('/proc/self/fd').iterdir())
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert (
        pytest.main(
            [str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin", "-p", "no:cacheprovider"]
        )
        == pytest.ExitCode.OK
    )


def test_cleanup_continues_after_base_exception(request: SubRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    class CleanupAbort(BaseException):
        pass

    owner = EngineOwner(request.session)
    node = request.node
    first = create_engine("sqlite://")
    second = create_engine("sqlite://")
    events: list[str] = []

    def fail() -> None:
        events.append("second")
        raise CleanupAbort("dispose failed")

    monkeypatch.setattr(first, "dispose", lambda: events.append("first"))
    monkeypatch.setattr(second, "dispose", fail)
    owner.engines[node] = [first, second]
    owner.temp_finalizers[node] = [lambda: events.append("temporary directory")]
    with pytest.raises(BaseExceptionGroup, match="engine disposal failed") as caught:
        owner.dispose(node)
    assert isinstance(caught.value.exceptions[0], CleanupAbort)
    assert events == ["second", "first", "temporary directory"]


def test_session_fallback_disposes_narrow_scopes_first_even_after_failure(
    request: SubRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = start(request.session)
    owner.engines[request.node] = []
    events: list[str] = []

    def dispose(node: object) -> None:
        if node is request.node:
            events.append("function")
            raise RuntimeError("scope failed")
        events.append("session")

    monkeypatch.setattr(owner, "dispose", dispose)
    with pytest.raises(ExceptionGroup, match="session engine disposal failed"):
        finish(owner)
    assert events == ["function", "session"]


def test_outer_thread_keeps_its_scope_during_nested_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    inner = tmp_path / "inner.py"
    inner.write_text("""\
import lifecycle_thread_sync as sync

def test_signal_outer_thread():
    sync.go.set()
    assert sync.done.wait(5)
""")
    outer = tmp_path / "outer.py"
    outer.write_text("""\
import os
import sys
from pathlib import Path
from threading import Event, Thread
from types import ModuleType
import pytest
from sqlalchemy import create_engine

root = Path(__file__).parent
path = root / 'outer-thread.db'

def opened():
    return any(p.exists() and os.path.realpath(p) == str(path) for p in Path('/proc/self/fd').iterdir())

def test_first(monkeypatch):
    sync = ModuleType('lifecycle_thread_sync')
    sync.go, sync.done = Event(), Event()
    monkeypatch.setitem(sys.modules, sync.__name__, sync)
    def worker():
        assert sync.go.wait(5)
        engine = create_engine(f'sqlite:///{path}')
        with engine.connect() as connection:
            connection.exec_driver_sql('select 1')
        sync.done.set()
    thread = Thread(target=worker)
    thread.start()
    try:
        assert pytest.main([str(root / 'inner.py'), '-q', '-c', '/dev/null', '-p', 'no:cacheprovider']) == 0
        assert opened()
    finally:
        sync.go.set()
        thread.join(5)
    assert not thread.is_alive()

def test_second():
    assert not opened()
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert pytest.main([str(outer), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"]) == pytest.ExitCode.OK


def test_nested_session_owns_engines_by_fixture_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = tmp_path / "test_scopes.py"
    module.write_text("""\
from pathlib import Path
import os
import pytest
from sqlalchemy import create_engine
from tests.support.runner_fakes import make_store

pytestmark = pytest.mark.unit
root = Path(__file__).parent

def open_fds(path):
    return [p for p in Path('/proc/self/fd').iterdir()
            if p.exists() and os.path.realpath(p) == str(path)]

@pytest.fixture(scope='module')
def shared():
    engine = create_engine(f"sqlite:///{root / 'shared.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
    yield engine
    assert open_fds(root / 'shared.db')

def test_first(shared):
    engine = create_engine(f"sqlite:///{root / 'function.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
    engine.dispose()
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
    assert open_fds(root / 'function.db')
    make_store(f"sqlite:///{root / 'helper.db'}")
    assert open_fds(root / 'helper.db')
    from threading import Thread
    def background():
        engine = create_engine(f"sqlite:///{root / 'thread.db'}")
        with engine.connect() as connection:
            connection.exec_driver_sql('select 1')
    thread = Thread(target=background)
    thread.start()
    thread.join()
    assert open_fds(root / 'thread.db')

def test_second(shared):
    assert not open_fds(root / 'function.db')
    assert not open_fds(root / 'helper.db')
    assert not open_fds(root / 'thread.db')
    assert open_fds(root / 'shared.db')
    with shared.connect() as connection:
        connection.exec_driver_sql('select 1')
""")
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main(
        [str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin", "-p", "no:cacheprovider"]
    )
    assert result == pytest.ExitCode.OK
    assert not any(p.exists() and p.resolve() == tmp_path / "shared.db" for p in Path("/proc/self/fd").iterdir())
    # The outer session still tracks engines after the nested session uninstalls.
    engine = create_engine(f"sqlite:///{tmp_path / 'outer.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("select 1")


def test_failed_setup_and_teardown_still_release_engines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    module = tmp_path / "test_failure.py"
    module.write_text("""\
from pathlib import Path
import pytest
from sqlalchemy import create_engine
pytestmark = pytest.mark.unit
root = Path(__file__).parent

def open_engine(name):
    engine = create_engine(f"sqlite:///{root / name}")
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')

@pytest.fixture
def bad_setup():
    open_engine('setup.db')
    raise ValueError('setup failure')

@pytest.fixture
def bad_teardown():
    open_engine('teardown.db')
    yield
    raise ValueError('teardown failure')

def test_setup(bad_setup):
    pass

def test_teardown(bad_teardown):
    pass
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main(
        [str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin", "-p", "no:cacheprovider"]
    )
    assert result == pytest.ExitCode.TESTS_FAILED
    paths = {tmp_path / "setup.db", tmp_path / "teardown.db"}
    assert not any(p.exists() and p.resolve() in paths for p in Path("/proc/self/fd").iterdir())


def test_class_fixture_engine_survives_tests_and_closes_after_finalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = tmp_path / "test_class_scope.py"
    module.write_text("""\
import pytest
from sqlalchemy import create_engine
from sqlalchemy import event

closed = []

@pytest.fixture(scope='class')
def shared():
    engine = create_engine('sqlite://')
    event.listen(engine, 'close', lambda *_: closed.append('closed'))
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
    yield engine
    assert not closed
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')

class TestShared:
    def test_first(self, shared):
        assert not closed

    def test_second(self, shared):
        assert not closed

def test_after_class():
    assert closed == ['closed']
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"]) == pytest.ExitCode.OK


def test_session_scope_and_production_factory_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe open SQLite handles")
    (tmp_path / "conftest.py").write_text("""\
from pathlib import Path
import pytest
from blizzard.foundation.store.engine import create_engine_from_url as build

root = Path(__file__).parent

@pytest.fixture(scope='session')
def shared():
    engine = build(f"sqlite:///{root / 'session.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
    yield engine
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
""")
    (tmp_path / "test_a.py").write_text("""\
from pathlib import Path
from sqlalchemy import create_engine as imported_factory
from blizzard.foundation.store.engine import create_engine_from_url as production_factory
root = Path(__file__).parent

def test_first(shared):
    for name, factory in [('alias.db', imported_factory), ('production.db', production_factory)]:
        engine = factory(f"sqlite:///{root / name}")
        with engine.connect() as connection:
            connection.exec_driver_sql('select 1')
""")
    (tmp_path / "test_b.py").write_text("""\
import os
from pathlib import Path
root = Path(__file__).parent

def opened(name):
    return any(p.exists() and os.path.realpath(p) == str(root / name) for p in Path('/proc/self/fd').iterdir())

def test_second(shared):
    assert opened('session.db')
    assert not opened('alias.db')
    assert not opened('production.db')
    with shared.connect() as connection:
        connection.exec_driver_sql('select 1')
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main([str(tmp_path), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"])
    assert result == pytest.ExitCode.OK
    assert not any(p.exists() and p.resolve() == tmp_path / "session.db" for p in Path("/proc/self/fd").iterdir())


def test_pluginless_nested_run_is_isolated_and_finalizer_engines_are_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe SQLite handles")
    module = tmp_path / "test_nested.py"
    module.write_text("""\
import os
from pathlib import Path
import pytest
from sqlalchemy import create_engine
root = Path(__file__).parent

def opened(path):
    return any(p.exists() and os.path.realpath(p) == str(path) for p in Path('/proc/self/fd').iterdir())

def create(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')

@pytest.fixture(scope='module')
def shared():
    yield
    create(root / 'module-finalizer.db')

@pytest.fixture
def fixture(shared):
    yield
    create(root / 'function-finalizer.db')

def test_first(fixture):
    pass

def test_second(shared):
    assert not opened(root / 'function-finalizer.db')
""")
    outer = create_engine(f"sqlite:///{tmp_path / 'outer-before.db'}")
    with outer.connect() as connection:
        connection.exec_driver_sql("select 1")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"])
    assert result == pytest.ExitCode.OK
    paths = {tmp_path / "function-finalizer.db", tmp_path / "module-finalizer.db"}
    assert not any(p.exists() and p.resolve() in paths for p in Path("/proc/self/fd").iterdir())
    assert any(p.exists() and p.resolve() == tmp_path / "outer-before.db" for p in Path("/proc/self/fd").iterdir())


def test_tmp_path_is_removed_after_finalizers_and_engine_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe SQLite handles")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\ntmp_path_retention_policy = failed\n")
    module = tmp_path / "test_order.py"
    module.write_text("""\
import os
from pathlib import Path
import pytest
from sqlalchemy import create_engine
pytestmark = pytest.mark.unit
root = Path(__file__).parent

@pytest.fixture
def resource(tmp_path):
    path = tmp_path / 'finalizer.db'
    (root / 'path.txt').write_text(str(path))
    yield
    assert tmp_path.exists()
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')

def test_first(resource):
    pass

def test_second():
    path = Path((root / 'path.txt').read_text())
    assert not path.parent.exists()
    targets = []
    for p in Path('/proc/self/fd').iterdir():
        try:
            targets.append(os.readlink(p))
        except FileNotFoundError:
            pass
    assert not any(target.startswith(str(path)) for target in targets)
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main(
        [str(module), "-q", "-c", str(config), "-p", "tests.support.suite_plugin", "-p", "no:cacheprovider"]
    )
    assert result == pytest.ExitCode.OK


def test_real_migration_prototypes_keep_process_lifetime_but_copies_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("requires procfs to observe SQLite handles")
    module = tmp_path / "test_prototypes.py"
    module.write_text("""\
import os
from contextlib import contextmanager
from pathlib import Path
import pytest
from sqlalchemy.engine import Engine
from blizzard.foundation.store.engine import create_engine_from_url
from tests.support import engine_lifecycle, runner_fakes, scaffolding
pytestmark = pytest.mark.unit
root = Path(__file__).parent
captured = []
pools = []
tempdirs = []

def opened(path):
    return any(p.exists() and os.path.realpath(p) == str(path) for p in Path('/proc/self/fd').iterdir())

def test_first(monkeypatch):
    # Force actual prototype construction without replacing a cache owned by the outer session.
    for module, prefix in [(runner_fakes, '_runner'), (scaffolding, '_hub')]:
        monkeypatch.setattr(module, prefix + '_prototype_db', None)
        monkeypatch.setattr(module, prefix + '_prototype_tmp', None)
    original = Engine.__init__
    active = False
    def capture(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if active:
            captured.append(self)
    monkeypatch.setattr(Engine, '__init__', capture)
    original_prototype = engine_lifecycle.prototype
    @contextmanager
    def observe():
        nonlocal active
        active = True
        try:
            with original_prototype():
                yield
        finally:
            active = False
    monkeypatch.setattr(engine_lifecycle, 'prototype', observe)
    runner = runner_fakes.runner_migration_prototype()
    hub = scaffolding.hub_migration_prototype()
    tempdirs.extend([runner_fakes._runner_prototype_tmp, scaffolding._hub_prototype_tmp])
    assert any(str(engine.url.database) == str(runner) for engine in captured)
    assert any(str(engine.url.database) == str(hub) for engine in captured)
    # The helper may explicitly dispose its engines; reopen them to prove the
    # owner does not later close an intentionally process-owned prototype pool.
    for engine in captured:
        with engine.connect() as connection:
            connection.exec_driver_sql('select 1')
        pools.append(engine.pool)
    runner_fakes.migrated_store_at(f"sqlite:///{root / 'copy.db'}")
    copy = create_engine_from_url(f"sqlite:///{root / 'copy.db'}")
    with copy.connect() as connection:
        connection.exec_driver_sql('select 1')
    assert opened(root / 'copy.db')

def test_second():
    assert not opened(root / 'copy.db')
    for engine, pool in zip(captured, pools):
        assert engine.pool is pool
        engine.dispose()
    for tempdir in tempdirs:
        tempdir.cleanup()
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main(
        [str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin", "-p", "no:cacheprovider"]
    )
    assert result == pytest.ExitCode.OK


def test_parametrized_module_instances_close_after_finalizers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = tmp_path / "test_parametrized.py"
    module.write_text("""\
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event

root = Path(__file__).parent
closed = []
finalized = []

def open_engine(name, label):
    engine = create_engine(f'sqlite:///{root / name}')
    event.listen(engine, 'close', lambda *_: closed.append(label))
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
    return engine

@pytest.fixture(scope='session')
def wider():
    engine = open_engine('shared.db', 'wider')
    yield engine
    assert set(closed) == {'first', 'first-finalizer', 'second', 'second-finalizer'}
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')

@pytest.fixture(scope='module', params=['first', 'second'])
def resource(request, wider):
    name = request.param
    if name == 'second':
        assert finalized == ['first']
        assert set(closed) == {'first', 'first-finalizer'}
        with wider.connect() as connection:
            connection.exec_driver_sql('select 1')
    engine = open_engine('shared.db', name)
    yield engine
    assert name not in closed
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
    open_engine(f'{name}-finalizer.db', f'{name}-finalizer')
    finalized.append(name)

def test_resource(resource):
    with resource.connect() as connection:
        connection.exec_driver_sql('select 1')
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    assert pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"]) == pytest.ExitCode.OK


@pytest.mark.parametrize("failure", ["setup", "teardown"])
def test_parametrized_module_failure_does_not_retain_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    module = tmp_path / f"test_parametrized_{failure}_failure.py"
    module.write_text("""\
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event
import lifecycle_failure_state as state

root = Path(__file__).parent
closed = state.closed

@pytest.fixture(scope='module', params=['first', 'second'])
def resource(request):
    name = request.param
    if name == 'second':
        assert closed == ['first']
        state.next_instance_checked = True
    engine = create_engine(f'sqlite:///{root / (name + ".db")}')
    event.listen(engine, 'close', lambda *_: closed.append(name))
    with engine.connect() as connection:
        connection.exec_driver_sql('select 1')
    if name == 'first' and state.failure == 'setup':
        raise ValueError('expected setup failure')
    yield engine
    assert name not in closed
    if name == 'first' and state.failure == 'teardown':
        raise ValueError('expected teardown failure')

def test_resource(resource):
    with resource.connect() as connection:
        connection.exec_driver_sql('select 1')
""")

    class FailureState(ModuleType):
        failure: str
        next_instance_checked: bool
        closed: list[str]

    state = FailureState("lifecycle_failure_state")
    state.failure = failure
    state.next_instance_checked = False
    state.closed = []
    monkeypatch.setitem(sys.modules, state.__name__, state)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "no:cacheprovider"])
    assert result == pytest.ExitCode.TESTS_FAILED
    assert f"expected {failure} failure" in capsys.readouterr().out
    if failure == "setup":
        assert state.next_instance_checked
    assert "first" in state.closed
