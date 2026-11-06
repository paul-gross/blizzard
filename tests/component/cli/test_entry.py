"""The console scripts end through the fast exit, with the exit code and output intact."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from tests.support.repo_files import repo_root

_BIN = Path(sys.executable).parent

# A child process: an inline click group run through the entry helper, plus a body per case.
_CHILD = """
import sys
import click
from blizzard.cli import entry

@click.command()
def grp() -> None:
{body}

sys.modules["_child"] = sys.modules["__main__"]
entry._run("_child:grp")
"""


def _run_child(body: str) -> subprocess.CompletedProcess[str]:
    indented = "\n".join("    " + line for line in body.strip().splitlines())
    script = _CHILD.format(body=indented) + "\n"
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        input="",
    )


def test_scripts_target_the_entry_functions() -> None:
    scripts = tomllib.loads((repo_root() / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["blizzard"] == "blizzard.cli.entry:blizzard"
    assert scripts["blizzard-hub"] == "blizzard.cli.entry:hub"
    assert scripts["blizzard-runner"] == "blizzard.cli.entry:runner"


def test_real_binary_success_and_usage_error_codes() -> None:
    ok = subprocess.run([_BIN / "blizzard", "--version"], capture_output=True, text=True, timeout=60, check=False)
    assert ok.returncode == 0
    assert "blizzard" in ok.stdout
    bad = subprocess.run([_BIN / "blizzard", "--no-such-flag"], capture_output=True, text=True, timeout=60, check=False)
    assert bad.returncode == 2
    assert "No such option" in bad.stderr
    for name in ("blizzard-hub", "blizzard-runner"):
        usage = subprocess.run([_BIN / name, "--no-such-flag"], capture_output=True, text=True, timeout=60, check=False)
        assert usage.returncode == 2
        assert "No such option" in usage.stderr
        done = subprocess.run([_BIN / name, "--help"], capture_output=True, text=True, timeout=60, check=False)
        assert done.returncode == 0
        assert "Usage" in done.stdout


def test_system_exit_code_is_kept() -> None:
    assert _run_child("import sys\nsys.exit(7)").returncode == 7


def test_uncaught_exception_exits_1_with_traceback() -> None:
    result = _run_child("raise RuntimeError('boom')")
    assert result.returncode == 1
    assert "RuntimeError: boom" in result.stderr


def test_output_larger_than_a_pipe_buffer_arrives_complete() -> None:
    result = _run_child("click.echo('x' * 1_000_000)")
    assert result.returncode == 0
    assert len(result.stdout.strip()) == 1_000_000


def test_exit_follows_a_non_daemon_thread_and_a_later_atexit_handler() -> None:
    body = """
import atexit, threading, time
def work():
    time.sleep(0.3)
    print("thread-done", flush=True)
threading.Thread(target=work).start()
atexit.register(lambda: print("atexit-done", flush=True))
"""
    result = _run_child(body)
    assert result.returncode == 0
    assert result.stdout.split() == ["thread-done", "atexit-done"]


def test_exit_follows_loggings_shutdown() -> None:
    body = """
import logging, logging.handlers, sys
target = logging.StreamHandler(sys.stdout)
buffered = logging.handlers.MemoryHandler(capacity=1000, flushLevel=logging.CRITICAL, target=target)
log = logging.getLogger("t")
log.addHandler(buffered)
log.setLevel(logging.INFO)
log.info("flushed-on-close")
"""
    result = _run_child(body)
    assert result.returncode == 0
    assert "flushed-on-close" in result.stdout


def test_detached_stdio_does_not_block_the_fast_exit() -> None:
    body = "import sys\nsys.stdout = None\nsys.stderr = None"
    assert _run_child(body).returncode == 0
