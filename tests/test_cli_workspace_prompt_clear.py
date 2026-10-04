"""``blizzard runner prompt clear`` — the one prompt verb that goes through the running daemon."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from blizzard.cli.main import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.test_runner_status_cli import _init_runner, _serve_local_api

pytestmark = pytest.mark.component


def _run(*args: str) -> Result:
    return CliRunner().invoke(blizzard, ["runner", "prompt", *args])


def test_clear_drops_the_override_through_the_daemon(tmp_path: Path) -> None:
    root = _init_runner(tmp_path)
    config = RunnerConfig.load(root)
    store = SqlAlchemyRunnerStore(create_engine_from_url(config.db_url), runner_store_errors())
    store.set_workspace_prompt(config.workspace_id, prompt="AN OVERRIDE", at=datetime(2026, 1, 1, tzinfo=UTC))
    standing = _run("status", "--dir", str(root))
    assert "store override" in standing.output
    assert "runner prompt clear" in standing.output

    with _serve_local_api(root):
        cleared = _run("clear", "--dir", str(root))

    assert cleared.exit_code == 0, cleared.output
    assert "store override" not in _run("status", "--dir", str(root)).output


def test_clear_without_a_daemon_names_the_missing_runner(tmp_path: Path) -> None:
    result = _run("clear", "--dir", str(_init_runner(tmp_path)))

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output
