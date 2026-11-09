"""Exercise the guard in disposable pytest processes, including xdist transport."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import open_file_guard


def nested_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_XDIST_")}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("leak", ["none", "sqlite", "deleted"])
def test_pluginless_nested_xdist_owns_worker_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], leak: str
) -> None:
    module = tmp_path / "test_nested_worker.py"
    module.write_text(f"""\
from sqlalchemy import create_engine
connections = []
def test_case(tmp_path):
    engine = create_engine(f"sqlite:///{{tmp_path / 'nested.db'}}")
    connection = engine.connect()
    if {leak!r} == 'sqlite':
        connections.append(connection)
    else:
        connection.close()
    if {leak!r} == 'deleted':
        path = tmp_path / 'vanished'
        connections.append(path.open('w'))
        path.unlink()
""")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    result = pytest.main([str(module), "-q", "-c", "/dev/null", "-p", "xdist.plugin", "-n", "1"])
    output = capsys.readouterr().out
    if leak != "none":
        assert result != pytest.ExitCode.OK
        assert ("nested.db" if leak == "sqlite" else "vanished (deleted)") in output
        assert "open-file guard: scope=" in output
    else:
        assert result == pytest.ExitCode.OK, output
        assert "scope=terminal worker=gw0" in output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
def test_inspection_respects_directory_boundary(tmp_path: Path) -> None:
    root = tmp_path / "root"
    sibling = tmp_path / "root-sibling"
    root.mkdir()
    sibling.mkdir()
    with (root / "inside").open("w") as inside, (sibling / "outside").open("w") as outside:
        (root / "inside").unlink()
        (sibling / "outside").unlink()
        findings = open_file_guard.inspect(root)
        assert any(f"fd={inside.fileno()} " in finding for finding in findings)
        assert not any(f"fd={outside.fileno()} " in finding for finding in findings)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_inspection_only_tolerates_vanished_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[OSError]
) -> None:
    readlink = os.readlink
    with (tmp_path / "open").open("w") as handle:

        def failing_readlink(path: os.PathLike[str]) -> str:
            if Path(path).name == str(handle.fileno()):
                raise error("deliberate procfs error")
            return readlink(path)

        with monkeypatch.context() as patch:
            patch.setattr(open_file_guard.os, "readlink", failing_readlink)
            if error is FileNotFoundError:
                assert open_file_guard.inspect(tmp_path) == []
            else:
                with pytest.raises(PermissionError, match="deliberate procfs error"):
                    open_file_guard.inspect(tmp_path)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("disable_guard", [False, True])
def test_deleted_file_detection_depends_on_guard(tmp_path: Path, disable_guard: bool) -> None:
    module = tmp_path / "test_bypass.py"
    module.write_text(f"""\
import pytest
from tests.support import open_file_guard
pytestmark = pytest.mark.unit
handles = []
def test_case(tmp_path):
    if {disable_guard}:
        open_file_guard.inspect = lambda *args, **kwargs: []
    path = tmp_path / "deleted"
    handles.append(path.open("w"))
    path.unlink()
""")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin"],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    if disable_guard:
        assert result.returncode == 0, output
    else:
        assert result.returncode != 0, output
        assert "open-file guard: scope=" in output and "deleted (deleted)" in output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("disable_disposal", [False, True])
def test_sqlite_cleanup_depends_on_disposal(tmp_path: Path, disable_disposal: bool) -> None:
    module = tmp_path / "test_disposal.py"
    module.write_text(f"""\
import pytest
from sqlalchemy import create_engine
pytestmark = pytest.mark.unit
def test_case(tmp_path):
    engine = create_engine(f"sqlite:///{{tmp_path / 'store.db'}}")
    with engine.connect() as connection:
        connection.exec_driver_sql("select 1")
    if {disable_disposal}:
        engine.dispose = lambda: None
""")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin"],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    if disable_disposal:
        assert result.returncode != 0, output
        assert "open-file guard: scope=" in output and "store.db" in output
    else:
        assert result.returncode == 0, output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("leak", [False, True])
def test_scope_guard_reports_deleted_file(tmp_path: Path, leak: bool) -> None:
    module = tmp_path / "test_case.py"
    module.write_text(f"""\
import pytest
pytestmark = pytest.mark.unit
handles = []
@pytest.fixture
def late(tmp_path):
    yield
    if {leak}:
        path = tmp_path / "vanished"
        handles.append(path.open("w"))
        path.unlink()
def test_case(late):
    pass
""")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin"],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    if leak:
        assert result.returncode != 0
        assert "open-file guard: scope=" in output and "test_case" in output
        assert "worker=main" in output and "pid=" in output and "fd=" in output and "vanished (deleted)" in output
    else:
        assert result.returncode == 0, output
        assert "scope=terminal worker=main" in output and "count=0" in output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("leak,teardown_failure", [(False, False), (True, False), (True, True)])
def test_shared_sqlite_path_checked_after_last_owner(tmp_path: Path, leak: bool, teardown_failure: bool) -> None:
    module = tmp_path / "test_shared.py"
    module.write_text(f"""\
import pytest
from sqlalchemy import create_engine
pytestmark = pytest.mark.unit
leaked = []
@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    path = tmp_path_factory.mktemp("shared") / "store.db"
    engine = create_engine(f"sqlite:///{{path}}")
    with engine.connect() as connection:
        connection.exec_driver_sql("select 1")
    yield path

@pytest.fixture
def finalizer():
    yield
    if {teardown_failure}:
        raise RuntimeError("deliberate teardown failure")

def test_shared(shared, finalizer):
    engine = create_engine(f"sqlite:///{{shared}}")
    connection = engine.connect()
    connection.exec_driver_sql("select 1")
    if {leak}:
        leaked.append(connection)
    else:
        connection.close()
""")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin"],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    if leak:
        assert result.returncode != 0, output
        assert "open-file guard: scope=::test_shared worker=main" not in output
        assert "open-file guard: scope=<Module:test_shared.py>" in output
        assert "fd=" in output and "store.db" in output
        if teardown_failure:
            assert "deliberate teardown failure" in output
    else:
        assert result.returncode == 0, output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
def test_disposal_failure_does_not_bypass_inspection(tmp_path: Path) -> None:
    module = tmp_path / "test_bad_dispose.py"
    module.write_text("""\
import pytest
from sqlalchemy import create_engine
pytestmark = pytest.mark.unit
leaked = []
def test_case(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    leaked.append(engine.connect())
    def fail():
        raise RuntimeError("deliberate disposal failure")
    engine.dispose = fail
""")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin"],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "deliberate disposal failure" in output
    assert "open-file guard: scope=" in output and "fd=" in output and "store.db" in output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
@pytest.mark.parametrize("leak", [False, True])
def test_existing_sqlite_sidecars_are_checked(tmp_path: Path, suffix: str, leak: bool) -> None:
    module = tmp_path / "test_sidecar.py"
    module.write_text(f"""\
import pytest
from sqlalchemy import create_engine
pytestmark = pytest.mark.unit
handles = []
def test_case(tmp_path):
    path = tmp_path / "store.db"
    engine = create_engine(f"sqlite:///{{path}}")
    with engine.connect() as connection:
        connection.exec_driver_sql("select 1")
    sidecar = (tmp_path / "store.db{suffix}").open("w")
    if {leak}:
        handles.append(sidecar)
    else:
        sidecar.close()
""")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin"],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    if leak:
        assert result.returncode != 0, output
        assert "open-file guard: scope=" in output and "fd=" in output and f"store.db{suffix}" in output
    else:
        assert result.returncode == 0, output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
def test_sqlite_leak_through_symlink_is_detected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "alias").symlink_to(target, target_is_directory=True)
    module = tmp_path / "test_symlink.py"
    module.write_text("""\
from pathlib import Path
import pytest
from sqlalchemy import create_engine
pytestmark = pytest.mark.unit
leaked = []
def test_case():
    path = Path(__file__).parent / 'alias' / 'store.db'
    engine = create_engine(f'sqlite:///{path}')
    leaked.append(engine.connect())
""")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-q", "-c", "/dev/null", "-p", "tests.support.suite_plugin"],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "open-file guard: scope=" in output and "fd=" in output and "store.db" in output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("leak", [False, True])
def test_worker_terminal_guard_reaches_controller(tmp_path: Path, leak: bool) -> None:
    module = tmp_path / "test_worker.py"
    module.write_text(f"""\
import pytest
pytestmark = pytest.mark.unit
handles = []
@pytest.fixture(scope="session", autouse=True)
def late(tmp_path_factory):
    yield
    if {leak}:
        path = tmp_path_factory.getbasetemp() / "terminal-vanished"
        handles.append(path.open("w"))
        path.unlink()
def test_case():
    pass
""")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "xdist.plugin",
            "-p",
            "tests.support.suite_plugin",
            "-n",
            "1",
        ],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert "open-file guard: scope=terminal worker=gw0" in output, output
    assert "worker=gw0 last-test=" in output and "test_case teardown=passed" in output, output
    if leak:
        assert result.returncode != 0
        assert "count=1" in output and "fd=" in output and "terminal-vanished (deleted)" in output
    else:
        assert result.returncode == 0, output
        assert "count=0" in output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("leak", [False, True])
def test_worker_last_teardown_scan_precedes_terminal_cleanup(tmp_path: Path, leak: bool) -> None:
    (tmp_path / "conftest.py").write_text(f"""\
import pytest
handles = []
@pytest.fixture(scope="session", autouse=True)
def late(tmp_path_factory):
    yield
    if {leak}:
        path = tmp_path_factory.getbasetemp() / "boundary-vanished"
        handles.append(path.open("w"))
        path.unlink()
@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish():
    for handle in handles:
        handle.close()
""")
    module = tmp_path / "test_boundary.py"
    module.write_text("import pytest\npytestmark = pytest.mark.unit\ndef test_case():\n    pass\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "xdist.plugin",
            "-p",
            "tests.support.suite_plugin",
            "-n",
            "1",
        ],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert "scope=terminal worker=gw0" in output and "count=0" in output, output
    assert "scope=last-teardown worker=gw0" in output, output
    if leak:
        assert result.returncode != 0, output
        assert "count=1" in output and "boundary-vanished (deleted)" in output, output
    else:
        assert result.returncode == 0, output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
@pytest.mark.parametrize("missing", ["scan", "terminal-count", "unsupported-count", "last", "test-count"])
def test_worker_missing_scan_evidence_fails(tmp_path: Path, missing: str) -> None:
    (tmp_path / "conftest.py").write_text(f"""\
import pytest
@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_testnodedown(node, error):
    if {missing!r} == "scan":
        node.workeroutput.pop("open_file_guard_last_scan", None)
    elif {missing!r} == "terminal-count":
        node.workeroutput.pop("open_file_guard_count", None)
    elif {missing!r} == "unsupported-count":
        node.workeroutput["open_file_guard_count"] = None
    elif {missing!r} == "last":
        node.workeroutput.pop("open_file_guard_last_teardown", None)
    else:
        node.workeroutput.pop("open_file_guard_teardown_count", None)
""")
    module = tmp_path / "test_missing.py"
    module.write_text("import pytest\npytestmark = pytest.mark.unit\ndef test_case():\n    pass\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "xdist.plugin",
            "-p",
            "tests.support.suite_plugin",
            "-n",
            "1",
        ],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    if missing == "scan":
        assert "missing last-teardown scan evidence" in output, output


def test_idle_worker_with_terminal_evidence_passes(tmp_path: Path) -> None:
    module = tmp_path / "test_idle.py"
    module.write_text("import pytest\npytestmark = pytest.mark.unit\ndef test_only():\n    pass\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "xdist.plugin",
            "-p",
            "tests.support.suite_plugin",
            "-n",
            "2",
        ],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "scope=terminal worker=gw0" in output and "scope=terminal worker=gw1" in output, output
    assert "missing last-test teardown evidence" not in output, output
    assert "last-test=" in output and "teardown=passed" in output, output


@pytest.mark.parametrize("evidence", ["open_file_guard", "open_file_guard_count", "open_file_guard_teardown_count"])
def test_idle_worker_missing_terminal_evidence_fails(tmp_path: Path, evidence: str) -> None:
    (tmp_path / "conftest.py").write_text(f"""\
import pytest
@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_testnodedown(node, error):
    if node.workeroutput.get("open_file_guard_teardown_count") == 0:
        node.workeroutput.pop({evidence!r}, None)
""")
    module = tmp_path / "test_idle.py"
    module.write_text("import pytest\npytestmark = pytest.mark.unit\ndef test_only():\n    pass\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "xdist.plugin",
            "-p",
            "tests.support.suite_plugin",
            "-n",
            "2",
        ],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert (
        "open-file guard: missing worker evidence" in output
        or "missing terminal scan evidence" in output
        or ("missing or inconsistent test count" in output)
    ), output


def test_failed_last_teardown_is_reported(tmp_path: Path) -> None:
    module = tmp_path / "test_failed_teardown.py"
    module.write_text("""\
import pytest
pytestmark = pytest.mark.unit
@pytest.fixture
def bad():
    yield
    raise RuntimeError("teardown failed")
def test_case(bad):
    pass
""")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "xdist.plugin",
            "-p",
            "tests.support.suite_plugin",
            "-n",
            "1",
        ],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "worker=gw0 last-test=" in output and "test_case teardown=failed" in output, output


def test_crashed_worker_has_missing_evidence(tmp_path: Path) -> None:
    module = tmp_path / "test_crash.py"
    module.write_text("import os\nimport pytest\npytestmark = pytest.mark.unit\ndef test_crash():\n    os._exit(9)\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "xdist.plugin",
            "-p",
            "tests.support.suite_plugin",
            "-n",
            "1",
            "--max-worker-restart=0",
        ],
        capture_output=True,
        text=True,
        env=nested_env(),
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "open-file guard: missing worker evidence: ['gw0']" in output
    assert "INTERNALERROR" not in output
