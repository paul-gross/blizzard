"""The subprocess-backed hub command runner adapter — component tier.

The one direct test of the real
:class:`~blizzard.hub.delivery.internal.hub_command_runner.SubprocessHubCommandRunner`
mechanism (everywhere else the executor is wired with a fake). The load-bearing
guarantee: a bare ``python3`` in a hub command node's ``run:`` step resolves to the
hub daemon's own interpreter — the one that can import ``blizzard`` — even when the
daemon's inherited ``PATH`` does not carry it, the shape a wheel-installed daemon
launched by absolute path under systemd actually has.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from blizzard.hub.delivery.internal.hub_command_runner import SubprocessHubCommandRunner

pytestmark = pytest.mark.component


def test_bare_python3_resolves_to_the_hubs_own_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    interpreter_bin = os.path.dirname(sys.executable)
    inherited = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p != interpreter_bin]
    monkeypatch.setenv("PATH", os.pathsep.join(inherited) or "/usr/bin:/bin")

    result = SubprocessHubCommandRunner().run(
        command='python3 -c "import sys; print(sys.executable)"',
        cwd=str(tmp_path),
        env={},
    )

    assert result.exit_code == 0, result.stderr
    assert os.path.dirname(result.stdout.strip()) == interpreter_bin


def test_an_injected_path_is_still_prepended_not_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    interpreter_bin = os.path.dirname(sys.executable)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = SubprocessHubCommandRunner().run(
        command='printf %s "$PATH"',
        cwd=str(tmp_path),
        env={},
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout.split(os.pathsep, 1)[0] == interpreter_bin
    assert "/usr/bin" in result.stdout
