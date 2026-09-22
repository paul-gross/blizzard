"""``SubprocessOneShotProcess.run`` — the production
:class:`~blizzard.runner.subscriptions.one_shot_process.IOneShotProcess` binding: a real
``argv`` fed ``stdin``, closed after an optional settle delay, under a timeout. Never raises."""

from __future__ import annotations

import ast
import os
import sys
import time

import pytest

from blizzard.runner.subscriptions.internal.subprocess_one_shot_process import SubprocessOneShotProcess

pytestmark = pytest.mark.unit

_INHERITED_ENV = dict(os.environ)


def test_stdin_is_fed_to_the_child_and_its_stdout_is_captured() -> None:
    result = SubprocessOneShotProcess().run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        stdin="hello\n",
        timeout=5.0,
        env=_INHERITED_ENV,
    )

    assert result.exit_code == 0
    assert result.stdout == "HELLO\n"
    assert result.timed_out is False


def test_a_nonzero_exit_is_reported_without_raising() -> None:
    result = SubprocessOneShotProcess().run(
        [sys.executable, "-c", "import sys; sys.exit(3)"], stdin="", timeout=5.0, env=_INHERITED_ENV
    )

    assert result.exit_code == 3
    assert result.timed_out is False


def test_a_hung_child_is_killed_and_reported_as_a_timeout() -> None:
    result = SubprocessOneShotProcess().run(
        [sys.executable, "-c", "import time; time.sleep(60)"], stdin="", timeout=0.2, env=_INHERITED_ENV
    )

    assert result.exit_code is None
    assert result.timed_out is True


def test_a_missing_binary_is_reported_without_raising() -> None:
    result = SubprocessOneShotProcess().run(["no-such-binary-anywhere"], stdin="", timeout=5.0, env=_INHERITED_ENV)

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
    # A platform's own `subprocess` may inject a locale variable (e.g. `LC_CTYPE`) beside an
    # explicit `env`; what matters is that nothing from this process's own environment leaked.
    keys = set(ast.literal_eval(result.stdout))
    assert "ONLY_THIS" in keys
    assert "PATH" not in keys
    assert "HOME" not in keys


def test_settle_seconds_delays_closing_stdin_past_the_write() -> None:
    """A child that keeps running until it reads EOF, and only then replies, gets that
    reply back — closing stdin waits ``settle_seconds`` past the write rather than
    happening immediately, so a reply still being produced is never cut off."""
    script = (
        "import sys, time\n"
        "start = time.monotonic()\n"
        "sys.stdin.read()\n"
        "sys.stdout.write(str(time.monotonic() - start))\n"
    )
    result = SubprocessOneShotProcess().run(
        [sys.executable, "-c", script], stdin="", timeout=5.0, env=_INHERITED_ENV, settle_seconds=0.3
    )

    assert result.exit_code == 0
    assert float(result.stdout) >= 0.25  # a monotonic clock read after process start, not before


def test_settle_seconds_zero_closes_immediately() -> None:
    start = time.monotonic()
    result = SubprocessOneShotProcess().run(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin="", timeout=5.0, env=_INHERITED_ENV
    )
    elapsed = time.monotonic() - start

    assert result.exit_code == 0
    assert elapsed < 1.0
