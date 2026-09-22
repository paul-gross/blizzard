"""``SubprocessOneShotProcess.run`` (blizzard#504) — the production
:class:`~blizzard.runner.subscriptions.one_shot_process.IOneShotProcess` binding: a real
``argv`` fed ``stdin`` then closed, under a timeout. Never raises."""

from __future__ import annotations

import ast
import sys

import pytest

from blizzard.runner.subscriptions.internal.subprocess_one_shot_process import SubprocessOneShotProcess

pytestmark = pytest.mark.unit


def test_stdin_is_fed_to_the_child_and_its_stdout_is_captured() -> None:
    result = SubprocessOneShotProcess().run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        stdin="hello\n",
        timeout=5.0,
    )

    assert result.exit_code == 0
    assert result.stdout == "HELLO\n"
    assert result.timed_out is False


def test_a_nonzero_exit_is_reported_without_raising() -> None:
    result = SubprocessOneShotProcess().run([sys.executable, "-c", "import sys; sys.exit(3)"], stdin="", timeout=5.0)

    assert result.exit_code == 3
    assert result.timed_out is False


def test_a_hung_child_is_killed_and_reported_as_a_timeout() -> None:
    result = SubprocessOneShotProcess().run(
        [sys.executable, "-c", "import time; time.sleep(60)"], stdin="", timeout=0.2
    )

    assert result.exit_code is None
    assert result.timed_out is True


def test_a_missing_binary_is_reported_without_raising() -> None:
    result = SubprocessOneShotProcess().run(["no-such-binary-anywhere"], stdin="", timeout=5.0)

    assert result.exit_code is None
    assert result.timed_out is False
    assert result.stderr


def test_env_fully_replaces_the_childs_environment() -> None:
    result = SubprocessOneShotProcess().run(
        [sys.executable, "-c", "import os, sys; sys.stdout.write(repr(sorted(os.environ.keys())))"],
        stdin="",
        timeout=5.0,
        env={"ONLY_THIS": "1"},
    )

    assert result.exit_code == 0
    # Some platforms' own `subprocess` implicitly injects a locale variable (e.g.
    # `LC_CTYPE`) alongside an explicit `env`; what matters is that nothing from this
    # test process's own environment (e.g. `PATH`, `HOME`) leaked through.
    keys = set(ast.literal_eval(result.stdout))
    assert "ONLY_THIS" in keys
    assert "PATH" not in keys
    assert "HOME" not in keys
