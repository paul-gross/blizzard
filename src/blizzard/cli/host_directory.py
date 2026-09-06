"""Reconcile ``host``'s positional DIRECTORY with its ``--dir`` option (issue #3).

Ranked per ``src/blizzard/cli/param_rank.py``: only a command-line tie that disagrees
is a usage error."""

from __future__ import annotations

from dataclasses import dataclass

import click

from blizzard.cli.param_rank import ParamSource


@dataclass(frozen=True)
class HostDirectory:
    """A ``host`` verb's two spellings of the same runtime directory."""

    directory: str | None
    dir_option: str

    @property
    def path(self) -> str:
        """The directory to use — a ``click.UsageError`` on a command-line tie that
        disagrees (ranked per ``src/blizzard/cli/param_rank.py``)."""
        if (
            self.directory is not None
            and ParamSource.of("dir_option").on_commandline
            and self.directory != self.dir_option
        ):
            raise click.UsageError(
                f"DIRECTORY ({self.directory!r}) and --dir ({self.dir_option!r}) disagree — pass one, not both"
            )
        return self.directory if self.directory is not None else self.dir_option
