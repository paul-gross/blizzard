"""``blizzard runner prompt clear`` — the workspace record's prompt settings returned to their defaults, through a
running host or into a stopped host's store."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import Result

from blizzard.cli.main import blizzard
from tests.support.host_files import load_runner_config
from tests.support.runner_cli_home import init_runner_home, serve_local_api
from tests.support.worker_http import bind_local_only

pytestmark = pytest.mark.usefixtures("short_host_sockets")


def _run(*args: str) -> Result:
    return bind_local_only().runner.invoke(blizzard, ["runner", *args])


def _set_prompt(root: Path) -> None:
    workspace_id = load_runner_config(root).home_workspace.id
    result = _run("workspace", "edit", workspace_id, 'prompt="A STANDING PROMPT"', "--dir", str(root))
    assert result.exit_code == 0, result.output


def test_clear_on_a_running_host_goes_through_it(tmp_path: Path) -> None:
    root = init_runner_home(tmp_path)
    _set_prompt(root)
    assert "from inline prompt" in _run("prompt", "status", "--dir", str(root)).output

    with serve_local_api(root):
        cleared = _run("prompt", "clear", "--dir", str(root))

    assert cleared.exit_code == 0, cleared.output
    assert "through the running host" in cleared.output
    assert "from none" in _run("prompt", "status", "--dir", str(root)).output


def test_clear_on_a_stopped_host_writes_its_store(tmp_path: Path) -> None:
    root = init_runner_home(tmp_path)
    _set_prompt(root)

    cleared = _run("prompt", "clear", "--dir", str(root))

    assert cleared.exit_code == 0, cleared.output
    assert "stopped host's store" in cleared.output
    assert "from none" in _run("prompt", "status", "--dir", str(root)).output
