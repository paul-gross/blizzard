"""Reconcile ``host``'s positional DIRECTORY with its ``--dir`` option (issue #3).

An explicit ``--dir`` on the command line beats its own envvar/default fallback, so a
bare positional works unchanged when ``--dir`` was never spelled out; only a
command-line tie that disagrees is a usage error."""

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
        """The directory to use — a ``click.UsageError`` when the positional and
        ``--dir`` were both spelled out on the command line and disagree."""
        if (
            self.directory is not None
            and ParamSource.of("dir_option").on_commandline
            and self.directory != self.dir_option
        ):
            raise click.UsageError(
                f"DIRECTORY ({self.directory!r}) and --dir ({self.dir_option!r}) disagree — pass one, not both"
            )
        return self.directory if self.directory is not None else self.dir_option
