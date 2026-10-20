"""A hub whose own line is below its window's floor refuses to start, naming both (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.cli.main import blizzard
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.cli import runtime as hub_cli_runtime

pytestmark = pytest.mark.component


def test_a_hub_below_its_floor_exits_naming_its_version_and_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "hub"
    hub_runtime.init_environment(root)
    monkeypatch.setattr(hub_cli_runtime, "__version__", "0.1.9")

    result = CliRunner().invoke(blizzard, ["hub", "host", "--dir", str(root)])

    assert result.exit_code != 0, result.output
    assert "0.1.9" in result.output and "0.2" in result.output, result.output
    assert "Traceback" not in result.output
    assert "serving blizzard-hub" not in result.output
