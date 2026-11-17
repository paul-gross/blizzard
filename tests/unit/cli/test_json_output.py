"""The shared ``--json`` helpers: one object out, one error object for a command-body failure."""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner
from pydantic import BaseModel

from blizzard.cli.json_output import emit_json, json_errors, json_option
from blizzard.foundation.unrecognized import Unrecognized


class Item(BaseModel):
    name: str = "default"
    note: str | None = "default"


class Doc(BaseModel):
    item: Item
    items: list[Item]
    kind: str | Unrecognized = "x"


def test_emit_leaves_out_what_was_never_sent_and_keeps_an_explicit_null() -> None:
    doc = Doc.model_validate({"item": {"note": None}, "items": [], "kind": "future-kind"})
    runner = CliRunner()

    @click.command()
    def show() -> None:
        emit_json(doc)

    out = runner.invoke(show).stdout
    assert json.loads(out) == {"item": {"note": None}, "items": [], "kind": "future-kind"}
    assert out.count("\n") == 1


def _command(raises: click.ClickException) -> click.Command:
    @click.command(params=[json_option("Print it.")])
    def run(as_json: bool) -> None:
        with json_errors(as_json):
            raise raises

    return run


@pytest.mark.parametrize(("exc", "code"), [(click.ClickException("boom"), 1), (click.UsageError("bad usage"), 2)])
def test_a_body_failure_under_json_is_one_error_object_with_its_exit_code(exc: click.ClickException, code: int) -> None:
    result = CliRunner().invoke(_command(exc), ["--json"])
    assert result.exit_code == code
    assert json.loads(result.stdout) == {"error": {"message": exc.format_message()}}


def test_a_body_failure_without_json_is_left_to_click() -> None:
    result = CliRunner().invoke(_command(click.ClickException("boom")), [])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "boom" in result.stderr
