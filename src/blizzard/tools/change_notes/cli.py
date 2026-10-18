"""``blizzard-change-notes`` — the change-note tooling's command group."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.tools.change_notes.tree import CHANGES_DIR
from blizzard.tools.change_notes.validate import validate


@click.group()
def main() -> None:
    """Tooling for the change notes under ``changes/``."""


@main.command()
def check() -> None:
    """Fail on any change note, or file under changes/, that breaks the format."""
    root = Path.cwd()
    if not (root / CHANGES_DIR).is_dir():
        raise click.UsageError(f"no {CHANGES_DIR}/ directory under {root}; run from the repository root")
    failures = validate(root)
    for failure in failures:
        click.echo(failure)
    if failures:
        click.echo(f"{len(failures)} change-note failure(s)", err=True)
        raise SystemExit(1)
    click.echo("change notes ok")


if __name__ == "__main__":
    main()
