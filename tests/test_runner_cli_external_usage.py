"""``blizzard runner external-usage probe`` — the diagnostic CLI's SLUG argument (blizzard#436).

SLUG is optional, defaulting to the legacy ``anthropic`` declaration every scaffolded runner
still carries, so every pre-existing invocation with no positional argument keeps working."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from blizzard.cli.main import blizzard
from blizzard.runner.subscriptions.subscription_sampler import SampleMiss, SampleMissReason

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


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (SampleMissReason.CREDENTIAL_LAPSED, "no sample: credential lapsed: log in again"),
        (SampleMissReason.CREDENTIAL_UNREADABLE, "no sample: credential unreadable"),
        (SampleMissReason.ENDPOINT_UNREACHABLE, "no sample: endpoint unreachable"),
        (SampleMissReason.RESPONSE_UNPARSEABLE, "no sample: response unparseable"),
    ],
)
def test_a_miss_prints_its_own_distinguishable_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: SampleMissReason, expected: str
) -> None:
    root = _runtime(tmp_path)

    class _MissSampler:
        def sample(self) -> SampleMiss:
            return SampleMiss(reason)

    monkeypatch.setattr("blizzard.runner.cli.external_usage.select_sampler", lambda *args, **kwargs: _MissSampler())

    result = _probe(root)

    assert result.exit_code == 0, result.output
    assert expected in result.output
