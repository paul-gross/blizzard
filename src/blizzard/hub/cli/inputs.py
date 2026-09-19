"""Input helpers shared across two or more hub CLI concept modules."""

from __future__ import annotations

from pathlib import Path

import click


def read_body_file(path: str) -> str:
    """PATH's contents, or stdin when PATH is ``-``."""
    if path == "-":
        return click.get_text_stream("stdin").read()
    try:
        return Path(path).read_text()
    except OSError as exc:
        raise click.ClickException(f"failed to read {path}: {exc}") from exc
