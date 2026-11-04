"""Names the unit and component ``test_add_retire`` modules share."""

from __future__ import annotations

from pathlib import Path

from click.testing import Result

from blizzard.cli.main import blizzard
from tests.runner_init_fakes import FakeInitHub, init_runner

_HUB = "http://hub.test:8421"


def _run(hub: FakeInitHub, *args: str) -> Result:  # pyright: ignore[reportUnusedFunction]
    return init_runner(hub).invoke(blizzard, ["runner", *args])


def _registered(tmp_path: Path, hub: FakeInitHub) -> Path:  # pyright: ignore[reportUnusedFunction]
    root = tmp_path / "host"
    result = _run(hub, "init", str(root), "--hub", _HUB)
    assert result.exit_code == 0, result.output
    return root
