"""``blizzard hub <cmd>`` — the registry: declares the root ``hub`` group and maps each
concept module's commands onto it as ``"<module>:<attribute>"``, imported only when a command
runs, so a client verb never loads the OpenTelemetry SDK or the hub's daemon stack."""

from __future__ import annotations

import click

from blizzard.cli.operator_trace import OperatorGroup
from blizzard.hub.cli.sessions.internal.session_file import SessionFile
from blizzard.hub.cli.sessions.service import SessionService

_CLI = "blizzard.hub.cli"

_COMMANDS = {
    "init": f"{_CLI}.runtime:init",
    "migrate": f"{_CLI}.runtime:migrate_cmd",
    "host": f"{_CLI}.runtime:host",
    "status": f"{_CLI}.status:status",
    "record-marker": f"{_CLI}.marker:record_marker",
    "rotate-signing-key": f"{_CLI}.auth:rotate_signing_key",
    "login": f"{_CLI}.auth:login",
    "logout": f"{_CLI}.auth:logout",
    "chunk": f"{_CLI}.chunk:chunk_group",
    "item": f"{_CLI}.item:item_group",
    "runner": f"{_CLI}.runner:runner_group",
    "graph": f"{_CLI}.graph:graph_group",
    "scope": f"{_CLI}.scope:scope_group",
    "secret": f"{_CLI}.secret:secret_group",
    "source": f"{_CLI}.source:source_group",
    "config": f"{_CLI}.config:config_group",
    "routine": f"{_CLI}.routine:routine_group",
    "run": f"{_CLI}.garden_run:run_group",
    "finding": f"{_CLI}.finding:finding_group",
    "garden-proposal": f"{_CLI}.garden_proposal:garden_proposal_group",
    "queue": f"{_CLI}.queue:queue_group",
    "decision": f"{_CLI}.decision:decision_group",
    "question": f"{_CLI}.question:question_group",
    "analytics": f"{_CLI}.analytics:analytics_group",
    "events": f"{_CLI}.events:events",
    "traces": f"{_CLI}.traces:traces_group",
    "egress": f"{_CLI}.egress:egress_group",
}


@click.group(cls=OperatorGroup, lazy=_COMMANDS, trace_root="hub", invoke_without_command=True)
@click.pass_context
def hub(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard hub."""
    # The composition root: built once, inherited as `ctx.obj` by every
    # subcommand's own context — a `SessionService` wrapping the one `SessionFile`,
    # so a read-only verb still narrows it to `IReadSessionStore` while login/logout pull
    # the full service off the same object (pinned by
    # tests/test_layering.py::test_session_file_is_named_only_at_its_composition_root).
    ctx.obj = SessionService(SessionFile.of())
    if ctx.invoked_subcommand is None:
        host = hub.get_command(ctx, "host")
        assert host is not None
        ctx.invoke(host)
