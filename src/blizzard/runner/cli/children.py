"""The launch seam's composition root for a runner command (``bzh:dependency-injection``)."""

from __future__ import annotations

import click

from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses

_PIN = "blizzard.runner.cli.children"


def command_children(ctx: click.Context) -> IChildProcesses:
    children: IChildProcesses | None = ctx.meta.get(_PIN)
    if children is None:
        children = ctx.meta[_PIN] = SubprocessChildProcesses()
    return children
