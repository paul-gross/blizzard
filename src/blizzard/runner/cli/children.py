"""The child-process launcher a runner command reaches its children through — the command's composition root for
the launch seam (``bzh:dependency-injection``)."""

from __future__ import annotations

import click

from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses

_PIN = "blizzard.runner.cli.children"


def command_children(ctx: click.Context) -> IChildProcesses:
    """The launcher pinned on the command chain's shared ``meta``, else the production one, pinned there."""
    children: IChildProcesses | None = ctx.meta.get(_PIN)
    if children is None:
        children = ctx.meta[_PIN] = SubprocessChildProcesses()
    return children
