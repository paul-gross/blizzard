"""Printing the warnings a record write answers when it named its forge by an old address field."""

from __future__ import annotations

from typing import Any

import click


def echo_warnings(body: Any) -> None:
    """Print each of the response ``body``'s ``warnings`` to stderr, so ``--json`` output stays one document."""
    for warning in body.get("warnings", []):
        click.echo(f"warning: {warning['message']}", err=True)
