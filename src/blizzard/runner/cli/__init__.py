"""``blizzard runner <cmd>`` — the registry: declares the root ``runner`` group and maps each
concept module's commands onto it as ``"<module>:<attribute>"``, imported only when a command
runs, so a worker verb never loads the OpenTelemetry SDK or a daemon's stack."""

from __future__ import annotations

import click

from blizzard.cli.operator_trace import OperatorGroup
from blizzard.runner.cli.worker_call import WorkerSession
from blizzard.runner.harness.wiring import harness_cli_groups

_CLI = "blizzard.runner.cli"

_COMMANDS = {
    "init": f"{_CLI}.runtime:init",
    "migrate": f"{_CLI}.runtime:migrate_cmd",
    "host": f"{_CLI}.runtime:host",
    "tick": f"{_CLI}.runtime:tick_cmd",
    "external-usage": f"{_CLI}.external_usage:external_usage_group",
    "heartbeat": f"{_CLI}.worker:heartbeat",
    "session-end": f"{_CLI}.worker:session_end",
    "ask": f"{_CLI}.worker:ask",
    "attach": f"{_CLI}.worker:attach",
    "work-items": f"{_CLI}.worker:work_items",
    "pm-items": f"{_CLI}.worker:pm_items",
    "chunk": f"{_CLI}.worker:chunk_group",
    "prompt": f"{_CLI}.prompt:prompt_group",
    "harness": f"{_CLI}.harness:harness_group",
    "transcript": f"{_CLI}.transcript:transcript_group",
    "artifact": f"{_CLI}.artifact:artifact_group",
    "garden": f"{_CLI}.garden:garden_group",
    "finding": f"{_CLI}.finding:finding_group",
    "scope": f"{_CLI}.scope:scope_group",
    "analytics": f"{_CLI}.analytics:analytics_group",
    "traces": f"{_CLI}.traces:traces_group",
    "status": f"{_CLI}.control:status",
    "pause": f"{_CLI}.control:pause",
    "start": f"{_CLI}.control:start",
    "takeover": f"{_CLI}.control:takeover",
    "requeue": f"{_CLI}.control:requeue",
    "selftest": f"{_CLI}.control:selftest",
    # Each harness binding mounts its own optional verb group.
    **harness_cli_groups(),
}


class _RunnerGroup(OperatorGroup):
    """The root of a runner command: it owns the process's :class:`WorkerSession`, so the one
    HTTP client and the command's span are finished — and the span sent — as the command ends,
    before click reports any error. An operator's command is traced by the :class:`OperatorGroup`
    beneath it; the two never both open a span, since a worker's environment is never an operator's."""

    def invoke(self, ctx: click.Context) -> object:
        return WorkerSession.begin(ctx).run(lambda: super(_RunnerGroup, self).invoke(ctx))


@click.group(cls=_RunnerGroup, lazy=_COMMANDS, trace_root="runner", invoke_without_command=True)
@click.pass_context
def runner(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard runner."""
    if ctx.invoked_subcommand is None:
        host = runner.get_command(ctx, "host")
        assert host is not None
        ctx.invoke(host)
