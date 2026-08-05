"""Reconcile ``host``'s positional DIRECTORY with its ``--dir`` option (issue #3).

An explicit ``--dir`` on the command line beats its own envvar/default fallback, so a
bare positional works unchanged when ``--dir`` was never spelled out; only a
command-line tie that disagrees is a usage error.
"""

from __future__ import annotations

import click
from click.core import ParameterSource

from blizzard.cli.param_rank import source_rank


def resolve_host_directory(directory: str | None, dir_option: str) -> str:
    """The runtime directory a ``host`` verb should use.

    Raises ``click.UsageError`` when both the positional and ``--dir`` were spelled
    out on the command line and disagree.
    """
    ctx = click.get_current_context()
    dir_option_on_commandline = source_rank(ctx.get_parameter_source("dir_option")) == source_rank(
        ParameterSource.COMMANDLINE
    )
    if directory is not None and dir_option_on_commandline and directory != dir_option:
        raise click.UsageError(f"DIRECTORY ({directory!r}) and --dir ({dir_option!r}) disagree — pass one, not both")
    return directory if directory is not None else dir_option
