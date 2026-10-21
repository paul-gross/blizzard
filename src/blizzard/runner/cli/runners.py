"""``blizzard runner add`` / ``retire`` / ``ingest`` — add a runner to a registered host, retire one from it, under the
host's own credential, or take one in from another store.

``add`` writes in the order that leaves no entry without its token: the hub's mint, then the token in ``.env``, then
``runners/<name>/``, then the appended ``[[runner]]``. ``retire`` retires at the hub, then forgets the runner's identity
row in the host's store, then removes the entry, so a crash between any two leaves an entry the hub refuses — which the
host halts — never a runner the hub still serves with no entry naming it, nor a store naming a runner no entry
configures, which a start would take for another host's."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.cli.operator_trace import OperatorTrace
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.foundation.store.migrations import StoreBelowFloorError
from blizzard.runner.cli.daemon import daemon_holding
from blizzard.runner.cli.directory import load_host
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.runtime import InitCollaborators, forget_identity, write_runner_token
from blizzard.runner.composition import build_runner_ingest
from blizzard.runner.config import SCAFFOLD_AGENTS, ProjectBinding
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import (
    SCAFFOLD_SUBSCRIPTION,
    HostConfig,
    runner_toml,
    scaffold_runner,
)
from blizzard.runner.host_file import append_runner, remove_runner
from blizzard.runner.host_layout import RUNNERS_DIRNAME, HostHeld, HostLayout, hold_host
from blizzard.runner.hub.client import (
    HostRefusalReason,
    HostRefused,
    HostRunnerRetireRefused,
    HubClientError,
    RunnerAddRefused,
)
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.ingest import IngestRequest
from blizzard.runner.transfer.refusals import IngestRefused

_DIR_OPTION = click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Host directory (overrides $BZ_RUNNER_DIR).",
)


@click.command("add")
@click.argument("name")
@_DIR_OPTION
@click.option(
    "--project",
    "projects",
    multiple=True,
    required=True,
    metavar="PROJECT",
    help="A project the runner serves, by id or slug; repeat for several.",
)
@click.option(
    "--workspace",
    "workspaces",
    multiple=True,
    metavar="WORKSPACE",
    help="The host's workspace each --project runs in: once for all of them, or once per --project in order. "
    "Default: the host's only workspace.",
)
@click.option(
    "--agents",
    "agents",
    multiple=True,
    type=click.IntRange(min=1),
    metavar="N",
    help=f"How many agents each --project runs at once: once for all, or once per --project. "
    f"Default: {SCAFFOLD_AGENTS}.",
)
@click.pass_context
def add(
    ctx: click.Context,
    name: str,
    directory: str,
    projects: tuple[str, ...],
    workspaces: tuple[str, ...],
    agents: tuple[int, ...],
) -> None:
    """Add runner NAME to this registered host at its hub, with no hub sign-in: its token goes to the host's `.env`
    as BZ_HUB_TOKEN_<NAME> and its `[[runner]]` to the host file. A running host starts it at its next start.

    Refused, with nothing added, on a host holding no host token, or when NAME or a --workspace is not one the host
    file allows."""
    config = load_host(directory)
    token = _host_token(config)
    try:
        config.check_new_runner(name)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    token_env = hub_token_variable(name)
    bindings = _bindings(config, projects, workspaces, agents)
    hub = config.hub_url
    collaborators = InitCollaborators.of(ctx)
    with collaborators.host_clients(hub) as host_clients:
        try:
            answer = host_clients(token).add_runner(name, projects=projects)
        except RunnerAddRefused as exc:
            raise click.ClickException(f"the hub at {hub} refused to add runner {name}: {exc.detail}") from exc
        except HubClientError as exc:
            raise click.ClickException(f"cannot add runner {name} at the hub at {hub}: {exc}") from exc
    if isinstance(answer, HostRefused):
        raise click.ClickException(_host_refused(answer, hub, f"add runner {name}"))
    retire = f"`blizzard runner retire --runner {answer.runner_id} --dir {config.root}`"
    try:
        write_runner_token(config.root, token_env, answer.token)
    except OSError as exc:
        raise click.ClickException(
            f"runner {name} ({answer.runner_id}) was added at {hub}, but its token was not written: {exc}; retire "
            f"it with `blizzard hub runner retire {answer.runner_id} --hub-url {hub}`"
        ) from exc
    (config.root / RUNNERS_DIRNAME / name).mkdir(parents=True, exist_ok=True)
    declared = {subscription.slug for subscription in config.subscriptions}
    entry = scaffold_runner(
        config.root,
        name,
        bindings=bindings,
        subscription=SCAFFOLD_SUBSCRIPTION.slug if SCAFFOLD_SUBSCRIPTION.slug in declared else None,
        runner_id=answer.runner_id,
    )
    try:
        append_runner(config.layout, runner_toml(entry), strict=config.strict)
    except ConfigError as exc:
        raise click.ClickException(
            f"runner {name} ({answer.runner_id}) was added at {hub} and its token written, but its [[runner]] was "
            f"not: {exc}. Add it by hand, or retire it with {retire}"
        ) from exc
    click.echo(f"added runner {name} ({answer.runner_id}) at {hub}; its token is in {config.root / ENV_FILENAME}")
    running = _running(ctx, config.layout)
    if running is not None:
        click.echo(f"{running}: it starts runner {name} at its next start")


@click.command("retire")
@_DIR_OPTION
@click.option("--runner", "runner", required=True, help="The host's runner to retire, by name or id.")
@click.option("--force", is_flag=True, help="Retire it even while it holds chunks, releasing them.")
@click.pass_context
def retire(ctx: click.Context, directory: str, runner: str, force: bool) -> None:
    """Retire one of this registered host's runners at its hub, then remove its `[[runner]]` from the host file. A
    running host halts it at its next tick, the hub refusing its token; the host's store forgets its identity, and
    its other rows stay there, inert.

    Refused, with nothing retired, on a host holding no host token, or while the runner holds chunks unless --force
    releases them."""
    config = load_host(directory)
    token = _host_token(config)
    try:
        entry = config.sole_runner(runner)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if entry.id is None:
        raise click.ClickException(
            f"runner {entry.name} has no id in {config.config_path}, so the hub holds no runner of it to retire; "
            "remove its [[runner]] by hand"
        )
    hub = config.hub_url
    with InitCollaborators.of(ctx).host_clients(hub) as host_clients:
        try:
            answer = host_clients(token).retire_runner(entry.id, force=force)
        except HostRunnerRetireRefused as exc:
            raise click.ClickException(
                f"runner {entry.name} ({entry.id}) still holds chunks, so nothing was retired: {exc.detail}. Re-run "
                "with --force to release them"
            ) from exc
        except HubClientError as exc:
            raise click.ClickException(f"cannot retire runner {entry.name} at the hub at {hub}: {exc}") from exc
    if isinstance(answer, HostRefused):
        raise click.ClickException(_host_refused(answer, hub, f"retire runner {entry.name} ({entry.id})"))
    released = f", releasing {', '.join(answer.released_chunk_ids)}" if answer.released_chunk_ids else ""
    forget_identity(config, entry.id)
    try:
        remove_runner(config.layout, entry.name, strict=config.strict)
    except ConfigError as exc:
        raise click.ClickException(
            f"runner {entry.name} ({entry.id}) was retired at {hub}{released}, but its [[runner]] was not removed: "
            f"{exc}. Remove it by hand"
        ) from exc
    click.echo(f"retired runner {entry.name} ({entry.id}) at {hub}{released}, and removed its [[runner]]")
    running = _running(ctx, config.layout)
    if running is not None:
        click.echo(f"{running}: it halts runner {entry.name} at its next tick")


@click.command("ingest")
@click.argument("source", type=click.Path(path_type=Path))
@_DIR_OPTION
@click.option("--runner", "runner", required=True, help="The runner to take in, by name or id.")
@click.option(
    "--workspace-id",
    "workspace_id",
    default=None,
    help="The new id the runner's workspace takes here, when this host gives its id to another workspace.",
)
@click.pass_context
def ingest(ctx: click.Context, source: Path, directory: str, runner: str, workspace_id: str | None) -> None:
    """Move one runner from SOURCE — another host's directory, a `store reset --runner` archive, or a runner directory
    that has not graduated — into this host:
    its rows, its workspace, subscription and `[[runner]]` where this host lacks them, and its token; then delete its
    rows from SOURCE, moving a source left with no runner aside. An archive's `[[runner]]` and token must already be
    here. Refused while either host runs, or the hub does not hold the runner under this host; a re-run completes an
    interrupted ingest."""
    config = load_host(directory)
    trace = OperatorTrace.source(ctx)
    with InitCollaborators.of(ctx).host_clients(config.hub_url) as host_clients:
        service = build_runner_ingest(
            config, host_clients(config.host_token.value), holder=lambda sock: daemon_holding(sock, trace)
        )
        try:
            outcome = service.run(IngestRequest(source=source, runner=runner, workspace_id=workspace_id))
        except (IngestRefused, StoreBelowFloorError) as exc:
            raise click.ClickException(str(exc)) from exc
        except ConfigError as exc:
            raise click.ClickException(f"cannot ingest runner {runner}: {exc}") from exc
    verb = "completed the ingest of" if outcome.resumed else "ingested"
    click.echo(f"{verb} runner {outcome.runner_name} ({outcome.runner_id}) from {source} into {config.root}")
    for moved in outcome.set_aside:
        click.echo(f"set aside {moved}")


def _host_token(config: HostConfig) -> str:
    """The host's own token, or the refusal naming how a host gets one."""
    if not config.host_token.value:
        raise click.ClickException(
            f"this host holds no host token ({HOST_TOKEN_VARIABLE}), so it cannot add or retire runners at its hub: "
            f"register it with `blizzard runner init {config.root}`, or add it with "
            f"`blizzard hub runner-host add <name> --hub-url {config.hub_url}` and put the printed line in "
            f"{config.root / ENV_FILENAME}"
        )
    return config.host_token.value


def _bindings(
    config: HostConfig, projects: tuple[str, ...], workspaces: tuple[str, ...], agents: tuple[int, ...]
) -> tuple[ProjectBinding, ...]:
    """One binding per project, each to its workspace and agent count; refused when a --workspace is not one the
    host declares, or when either option is given neither once nor once per --project."""
    declared = [workspace.id for workspace in config.workspaces]
    if not workspaces:
        if len(declared) != 1:
            raise click.ClickException(
                f"this host declares {len(declared)} workspaces ({', '.join(declared) or 'none'}); name the one each "
                "--project runs in with --workspace"
            )
        workspaces = (declared[0],)
    for workspace in workspaces:
        if workspace not in declared:
            raise click.ClickException(
                f"--workspace {workspace!r} is not a workspace this host declares ({', '.join(declared)})"
            )
    counts = agents or (SCAFFOLD_AGENTS,)
    return tuple(
        ProjectBinding(project, _nth(workspaces, i, "--workspace", projects), _nth(counts, i, "--agents", projects))
        for i, project in enumerate(projects)
    )


def _nth[T](values: tuple[T, ...], index: int, option: str, projects: tuple[str, ...]) -> T:
    """The ``index``-th of ``values`` when one is given per project, or the only one."""
    if len(values) == 1:
        return values[0]
    if len(values) != len(projects):
        raise click.ClickException(f"give {option} once, or once per --project ({len(projects)})")
    return values[index]


def _host_refused(answer: HostRefused, hub: str, act: str) -> str:
    if answer.reason is HostRefusalReason.NOT_FOUND:
        return (
            f"the hub at {hub} answered not-found, so it did not {act}: the runner does not stand under this host, "
            f"or the hub predates hosts ({answer.detail})"
        )
    return (
        f"the hub at {hub} refuses this host's token, so it did not {act}: re-register the host with "
        f"`blizzard runner init`, or mint it a new token with `blizzard hub runner-host enroll <host_id> --hub-url "
        f"{hub}` ({answer.detail})"
    )


def _running(ctx: click.Context, layout: HostLayout) -> str | None:
    """Whether a host is running in ``layout``'s directory — its lock held, or its socket answering — in words."""
    trace = OperatorTrace.source(ctx)
    try:
        with hold_host(layout, (layout.socket_path,), lambda sock: daemon_holding(sock, trace)):
            return None
    except HostHeld as exc:
        return f"a running host holds {layout.root} ({exc})"
