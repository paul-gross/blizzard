"""``blizzard runner external-usage probe`` — the diagnostic CLI's SLUG argument (blizzard#436).

SLUG is optional, defaulting to the legacy ``anthropic`` declaration every scaffolded runner
still carries, so every pre-existing invocation with no positional argument keeps working."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from blizzard.cli.main import blizzard

pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    root.mkdir()
    result = CliRunner().invoke(blizzard, ["runner", "init", str(root)])
    assert result.exit_code == 0, result.output
    return root


def _probe(root: Path, *args: str) -> Result:
    return CliRunner().invoke(blizzard, ["runner", "external-usage", "probe", *args, "--dir", str(root)])


def test_no_slug_argument_resolves_the_legacy_anthropic_declaration(tmp_path: Path) -> None:
    root = _runtime(tmp_path)

    result = _probe(root)

    assert result.exit_code == 0, result.output
    assert "no known sampler binding" not in result.output


def test_an_unknown_slug_still_errors_by_name(tmp_path: Path) -> None:
    root = _runtime(tmp_path)

    result = _probe(root, "mystery")

    assert result.exit_code != 0
    assert "mystery" in result.output
