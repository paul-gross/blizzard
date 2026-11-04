"""``blizzard runner takeover`` — the grammar of the verb and the commands it composes."""

from __future__ import annotations

import shlex
from pathlib import Path

import click
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.lifecycle.takeover import TakeoverCommand


def test_takeover_cli_still_declares_the_dir_flag_and_a_chunk_id_argument() -> None:
    """Pins the CLI-flag shape ``TakeoverCommand`` hard-codes against the
    REAL Click command object, not the composed string — a rename of ``--dir`` or the
    chunk id here would silently break the board command while other tests stay green."""
    takeover_cmd = runner_group.get_command(click.Context(runner_group), "takeover")
    assert takeover_cmd is not None

    directory_param = next(p for p in takeover_cmd.params if p.name == "directory")
    assert directory_param.opts == ["--dir"]

    arguments = [p for p in takeover_cmd.params if isinstance(p, click.Argument)]
    assert [a.name for a in arguments] == ["chunk_id"]


def test_composed_wrapped_command_parses_through_the_real_takeover_grammar() -> None:
    """``TakeoverCommand`` composes the string the board renders; this parses
    that exact string through the REAL Click command's own argument parsing, so a
    coordinated edit the grammar rejects fails here even if string-equality tests pass."""
    composed = TakeoverCommand("ch_1", "/var/lib/blizzard/runner dir").wrapped
    argv = shlex.split(composed)
    assert argv[:3] == ["blizzard", "runner", "takeover"]

    takeover_cmd = runner_group.get_command(click.Context(runner_group), "takeover")
    assert takeover_cmd is not None
    with takeover_cmd.make_context("takeover", argv[3:]) as click_ctx:
        assert click_ctx.params["chunk_id"] == "ch_1"
        # The whitespace-bearing dir round-trips through quote -> shell-split -> parse.
        assert click_ctx.params["directory"] == "/var/lib/blizzard/runner dir"
        assert click_ctx.params["runner"] is None


def test_composed_wrapped_command_names_its_runner_through_the_real_takeover_grammar() -> None:
    composed = TakeoverCommand("ch_1", "/var/lib/blizzard/runner dir", "r claude").wrapped

    takeover_cmd = runner_group.get_command(click.Context(runner_group), "takeover")
    assert takeover_cmd is not None
    with takeover_cmd.make_context("takeover", shlex.split(composed)[3:]) as click_ctx:
        assert click_ctx.params["runner"] == "r claude"


def test_takeover_end_rejects_force(tmp_path: Path) -> None:
    result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--end", "--force", "--dir", str(tmp_path)])

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output
