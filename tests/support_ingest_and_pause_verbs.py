"""Names the unit and component ``test_ingest_and_pause_verbs`` modules share."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.cli import runner as runner_group
from tests.runner_init_fakes import init_runner


def _init_runner(tmp_path: Path) -> Path:  # pyright: ignore[reportUnusedFunction]
    """An initialized runner — init joined it to the hub, so the host file names its id and its store holds its
    identity row, and the local API writes a store bound to it."""
    root = tmp_path / "runner"
    result = init_runner().invoke(runner_group, ["init", str(root)])
    assert result.exit_code == 0, result.output
    return root
