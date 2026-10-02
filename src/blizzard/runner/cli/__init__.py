"""``blizzard runner <cmd>`` — the registry: declares the root ``runner`` group and
maps each concept module's commands onto it.

The registry is lazy: a command's module is imported only when that command runs, is listed or is
described. A worker verb therefore loads its own module and never the OpenTelemetry SDK or the
daemons' stacks that the host and control verbs reach. A command added here is named as
``"<module>:<attribute>"`` and its module must not import a heavy dependency at module level
where a worker verb's module would."""

from __future__ import annotations

import click

from blizzard.cli.lazy_group import LazyGroup

_CLI = "blizzard.runner.cli"

_COMMANDS = {
    "init": f"{_CLI}.runtime:init",
    "migrate": f"{_CLI}.runtime:migrate_cmd",
    "host": f"{_CLI}.runtime:host",
    "tick": f"{_CLI}.runtime:tick_cmd",
    "external-usage": f"{_CLI}.external_usage:external_usage_group",
    "opencode": f"{_CLI}.opencode:opencode_group",
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
}


@click.group(cls=LazyGroup, lazy=_COMMANDS, invoke_without_command=True)
@click.pass_context
def runner(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard runner."""
    if ctx.invoked_subcommand is None:
        host = runner.get_command(ctx, "host")
        assert host is not None
        ctx.invoke(host)
