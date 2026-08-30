"""``blizzard runner <cmd>`` — the registry: declares the root ``runner`` group and
composes each concept module's commands onto it."""

from __future__ import annotations

import click

from blizzard.runner.cli.artifact import artifact_group
from blizzard.runner.cli.control import pause, requeue, selftest, start, status, takeover
from blizzard.runner.cli.external_usage import external_usage_group
from blizzard.runner.cli.prompt import prompt_group
from blizzard.runner.cli.runtime import host, init, migrate_cmd, tick_cmd
from blizzard.runner.cli.transcript import transcript_group
from blizzard.runner.cli.worker import ask, attach, chunk_group, heartbeat, pm_items, session_end, work_items


@click.group(invoke_without_command=True)
@click.pass_context
def runner(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard runner."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(host)


runner.add_command(init)
runner.add_command(migrate_cmd)
runner.add_command(host)
runner.add_command(tick_cmd)

runner.add_command(external_usage_group)

runner.add_command(heartbeat)
runner.add_command(session_end)
runner.add_command(ask)
runner.add_command(attach)
runner.add_command(work_items)
runner.add_command(pm_items)
runner.add_command(chunk_group)

runner.add_command(prompt_group)
runner.add_command(transcript_group)
runner.add_command(artifact_group)

runner.add_command(status)
runner.add_command(pause)
runner.add_command(start)
runner.add_command(takeover)
runner.add_command(requeue)
runner.add_command(selftest)
