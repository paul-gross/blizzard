"""CLI smoke — the verb surface exists and the real verbs work (unit tier).

The scaffold implements ``init`` / ``migrate`` / ``host`` for real; the remaining
design/cli.md verbs are present as self-naming stubs. This exercises the wiring,
not the daemon runtime (``host`` blocks on a server and is not driven here).
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from blizzard.cli.main import blizzard


def test_root_lists_hub_and_runner() -> None:
    result = CliRunner().invoke(blizzard, ["--help"])
    assert result.exit_code == 0
    assert "hub" in result.output
    assert "runner" in result.output


def test_hub_lists_its_verbs() -> None:
    result = CliRunner().invoke(blizzard, ["hub", "--help"])
    assert result.exit_code == 0
    for verb in ("init", "migrate", "host", "status", "answer", "ingest", "promote", "requeue"):
        assert verb in result.output


def test_runner_lists_its_verbs() -> None:
    result = CliRunner().invoke(blizzard, ["runner", "--help"])
    assert result.exit_code == 0
    for verb in ("init", "migrate", "host", "heartbeat", "ask", "takeover"):
        assert verb in result.output


def test_hub_init_and_migrate(tmp_path: Path) -> None:
    runner = CliRunner()
    root = str(tmp_path / "hub")

    init_result = runner.invoke(blizzard, ["hub", "init", root])
    assert init_result.exit_code == 0, init_result.output
    assert (tmp_path / "hub" / "blizzard-hub.toml").exists()

    migrate_result = runner.invoke(blizzard, ["hub", "migrate", "--dir", root])
    assert migrate_result.exit_code == 0, migrate_result.output


def test_runner_init(tmp_path: Path) -> None:
    root = str(tmp_path / "runner")
    result = CliRunner().invoke(blizzard, ["runner", "init", root])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "runner" / "blizzard-runner.toml").exists()


def test_stub_verb_reports_not_implemented() -> None:
    # `runner status` is still a scaffold stub (ingest and the declarative pause are
    # implemented in this wave); a stub names itself.
    result = CliRunner().invoke(blizzard, ["runner", "status"])
    assert result.exit_code != 0
    assert "not yet implemented" in result.output
