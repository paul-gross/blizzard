"""Lazy OpenCode command selection."""

import click

from blizzard.cli.lazy_group import LazyGroup


@click.group(
    "opencode",
    cls=LazyGroup,
    lazy={
        "drive": "blizzard.runner.cli.opencode_drive:opencode_drive",
        "attend": "blizzard.runner.cli.opencode:opencode_attend",
        "compatibility": "blizzard.runner.cli.opencode:opencode_compatibility",
    },
)
def opencode_group() -> None:
    """OpenCode's admitted-version compatibility diagnostics, for either major version, the OpenCode 2 turn driver, and
    OpenCode 2 takeover."""
