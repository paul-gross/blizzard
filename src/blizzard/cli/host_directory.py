"""Reconcile ``host``'s positional DIRECTORY with its ``--dir`` option (issue #3).

``init`` takes a positional DIRECTORY; ``host`` historically took only ``--dir``, so a
user naturally mirrors ``init``'s spelling and hits click's "Got unexpected extra
argument" error. Both daemons' ``host`` verb now accept either, reconciled here so the
two CLIs (``blizzard.hub.cli`` / ``blizzard.runner.cli``) don't each carry their own copy
of the rule: an explicit ``--dir`` on the command line beats its own envvar/default
fallback — the same explicit-beats-ambient ranking ``blizzard.runner.cli``'s
``--dir``/``--runner-url`` seam already applies (``param_rank.py``) — so a bare
positional works unchanged when ``--dir`` was never spelled out, and only a genuine
command-line tie that disagrees is a usage error naming both.
"""

from __future__ import annotations

import click
from click.core import ParameterSource

from blizzard.cli.param_rank import source_rank


def resolve_host_directory(directory: str | None, dir_option: str) -> str:
    """The runtime directory a ``host`` verb should use.

    ``directory`` is the positional DIRECTORY argument's value (``None`` when omitted);
    ``dir_option`` is ``--dir``'s value, which already carries its own envvar/default
    fallback (so it is never ``None``). Raises ``click.UsageError`` when both were
    spelled out on the command line and disagree.
    """
    ctx = click.get_current_context()
    dir_option_on_commandline = source_rank(ctx.get_parameter_source("dir_option")) == source_rank(
        ParameterSource.COMMANDLINE
    )
    if directory is not None and dir_option_on_commandline and directory != dir_option:
        raise click.UsageError(f"DIRECTORY ({directory!r}) and --dir ({dir_option!r}) disagree — pass one, not both")
    return directory if directory is not None else dir_option
