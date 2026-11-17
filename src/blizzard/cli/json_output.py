"""The ``--json`` flag and its one-object output, shared by every CLI that offers it."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager

import click
from pydantic import BaseModel

#: The ``--json`` of a verb that prints the daemon's raw answer.
RAW_BODY_HELP = "Print the raw response body as JSON."


def json_option(help_text: str = RAW_BODY_HELP) -> click.Option:
    """The ``--json`` flag, delivered to the command as ``as_json``."""
    return click.Option(["--json", "as_json"], is_flag=True, default=False, help=help_text)


def emit_json(value: BaseModel) -> None:
    """Print ``value`` as exactly one JSON object, leaving out every field it was built without."""
    click.echo(json.dumps(value.model_dump(mode="json", exclude_unset=True)))


@contextmanager
def json_errors(as_json: bool) -> Generator[None]:
    """Under ``as_json``, turn a ``click.ClickException`` raised in the body into one ``{"error": {"message": …}}``
    object on stdout, exiting with that exception's own code; otherwise leave it to click."""
    try:
        yield
    except click.ClickException as exc:
        if not as_json:
            raise
        click.echo(json.dumps({"error": {"message": exc.format_message()}}))
        raise click.exceptions.Exit(exc.exit_code) from None
