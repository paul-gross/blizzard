"""Offline invariant command over disposable stopped hosts."""

from pathlib import Path

from click.testing import CliRunner

from blizzard.cli.main import blizzard
from tests.support.runner_invariant_specimens import corrupt, healthy


def test_offline_runner_verdicts(tmp_path: Path) -> None:
    root = tmp_path / "host"
    healthy(root)
    runner = CliRunner()
    args = ["dev", "check-invariants", "--runner-dir", str(root)]
    clean = runner.invoke(blizzard, args)
    assert clean.exit_code == 0, clean.output
    assert "invariants hold" in clean.output

    corrupt(root)
    broken = runner.invoke(blizzard, args)
    assert broken.exit_code == 1, broken.output
    assert "runner:unique-env-binding" in broken.output

    restored = tmp_path / "restored"
    healthy(restored)
    clean_again = runner.invoke(blizzard, ["dev", "check-invariants", "--runner-dir", str(restored)])
    assert clean_again.exit_code == 0, clean_again.output
    assert "invariants hold" in clean_again.output
