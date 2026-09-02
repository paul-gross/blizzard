"""``blizzard hub <cmd>`` — the registry: declares the root ``hub`` group and
composes each concept module's commands onto it."""

from __future__ import annotations

import click

from blizzard.hub.cli.analytics import analytics_group
from blizzard.hub.cli.auth import login as _login_command
from blizzard.hub.cli.auth import logout, rotate_signing_key
from blizzard.hub.cli.chunk import chunk_group
from blizzard.hub.cli.decision import decision_group
from blizzard.hub.cli.finding import finding_group
from blizzard.hub.cli.garden_proposal import garden_proposal_group
from blizzard.hub.cli.garden_run import run_group
from blizzard.hub.cli.graph import graph_group
from blizzard.hub.cli.item import item_group
from blizzard.hub.cli.marker import record_marker
from blizzard.hub.cli.question import question_group
from blizzard.hub.cli.queue import queue_group
from blizzard.hub.cli.routine import routine_group
from blizzard.hub.cli.runner import runner_group
from blizzard.hub.cli.runtime import host, init, migrate_cmd
from blizzard.hub.cli.scope import scope_group
from blizzard.hub.cli.status import status as _status_command


@click.group(invoke_without_command=True)
@click.pass_context
def hub(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard hub."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(host)


hub.add_command(init)
hub.add_command(migrate_cmd)
hub.add_command(host)
hub.add_command(_status_command)
hub.add_command(record_marker)
hub.add_command(rotate_signing_key)
hub.add_command(_login_command)
hub.add_command(logout)

hub.add_command(chunk_group)
hub.add_command(item_group)
hub.add_command(runner_group)
hub.add_command(graph_group)
hub.add_command(scope_group)
hub.add_command(routine_group)
hub.add_command(run_group)
hub.add_command(finding_group)
hub.add_command(garden_proposal_group)
hub.add_command(queue_group)
hub.add_command(decision_group)
hub.add_command(question_group)
hub.add_command(analytics_group)
